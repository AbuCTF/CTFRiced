from flask import Blueprint
from CTFd.models import Challenges, Solves, Fails, db
from CTFd.plugins import register_plugin_assets_directory, register_plugin_script, register_plugin_stylesheet, register_admin_plugin_stylesheet, register_admin_plugin_script
from CTFd.plugins.challenges import CHALLENGE_CLASSES, BaseChallenge, ChallengeResponse
from CTFd.utils.modes import get_model
from CTFd.utils.user import get_ip
from CTFd.plugins.migrations import upgrade
import math
from sqlalchemy import Numeric, inspect, text

# Patch CTFd's Challenge view to include additional fields
from CTFd.api.v1.challenges import Challenge as ChallengeAPI
original_challenge_get = ChallengeAPI.get


def get_solve_count(challenge):
    Model = get_model()

    solve_count = (
        Solves.query.join(Model, Solves.account_id == Model.id)
        .filter(
            Solves.challenge_id == challenge.id,
            Model.hidden == False,
            Model.banned == False,
        )
        .count()
    )
    return solve_count


def patched_challenge_get(self, challenge_id):
    response = original_challenge_get(self, challenge_id)
    
    # Check if this is a geo challenge and add the tolerance_radius
    if isinstance(response, dict) and response.get('success'):
        challenge_data = response.get('data', {})
        if challenge_data.get('type') == 'geo':
            challenge = GeoChallenge.query.filter_by(id=challenge_id).first()
            if challenge:
                response['data']['tolerance_radius'] = float(challenge.tolerance_radius)
                # Assurer que les coordonnées sont retournées avec leur précision complète
                response['data']['latitude'] = float(challenge.latitude)
                response['data']['longitude'] = float(challenge.longitude)
    
    return response

# Apply the patch
ChallengeAPI.get = patched_challenge_get

class GeoChallenge(Challenges):
    __mapper_args__ = {"polymorphic_identity": "geo"}
    id = db.Column(
        db.Integer, db.ForeignKey("challenges.id", ondelete="CASCADE"), primary_key=True
    )
    latitude = db.Column(Numeric(12, 10), default=0)
    longitude = db.Column(Numeric(13, 10), default=0)
    tolerance_radius = db.Column(Numeric(10, 2), default=10)
    
    # Dynamic scoring fields (optional)
    initial = db.Column(db.Integer, default=None)
    minimum = db.Column(db.Integer, default=None)
    decay = db.Column(db.Integer, default=None)

    def __init__(self, *args, **kwargs):
        # Extract geo-specific parameters before passing to parent
        self.latitude = kwargs.pop('latitude', 0)
        self.longitude = kwargs.pop('longitude', 0)
        self.tolerance_radius = kwargs.pop('tolerance_radius', 10)
        
        # Extract dynamic scoring parameters (for dynamic scoring support)
        self.initial = kwargs.pop('initial', None)
        self.minimum = kwargs.pop('minimum', None)
        self.decay = kwargs.pop('decay', None)
        
        # Only allow known valid parameters for the base Challenge model
        valid_challenge_params = {
            'name', 'description', 'value', 'category', 'type', 'state'
        }
        
        # Filter kwargs to only include valid parameters
        filtered_kwargs = {k: v for k, v in kwargs.items() if k in valid_challenge_params}
        
        # Pass only valid kwargs to parent class
        super(GeoChallenge, self).__init__(**filtered_kwargs)


class GeoChallengeType(BaseChallenge):
    id = "geo"
    name = "geo"
    templates = {
        "create": "/plugins/geo_challenges/assets/create.html",
        "update": "/plugins/geo_challenges/assets/update.html",
        "view": "/plugins/geo_challenges/assets/view.html",
    }
    scripts = {
        "create": "/plugins/geo_challenges/assets/create.js",
        "update": "/plugins/geo_challenges/assets/update.js",
        "view": "/plugins/geo_challenges/assets/view.js",
    }
    route = "/plugins/geo_challenges/assets/"
    blueprint = Blueprint(
        "geo_challenges",
        __name__,
        template_folder="templates",
        static_folder="assets",
    )
    challenge_model = GeoChallenge

    @classmethod
    def calculate_distance(cls, lat1, lon1, lat2, lon2):
        """Calculate distance between two points using Haversine formula"""
        R = 6371e3  # Earth's radius in meters

        φ1 = math.radians(lat1)
        φ2 = math.radians(lat2)
        Δφ = math.radians(lat2 - lat1)
        Δλ = math.radians(lon2 - lon1)

        a = (math.sin(Δφ/2) * math.sin(Δφ/2) +
             math.cos(φ1) * math.cos(φ2) *
             math.sin(Δλ/2) * math.sin(Δλ/2))
        
        c = 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))
        return R * c  # Distance in meters

    @classmethod
    def calculate_value(cls, challenge):
        """Calculate the current value of the challenge (supports dynamic scoring)"""
        # If dynamic scoring parameters are not set, return the base value
        if not all([challenge.initial, challenge.minimum, challenge.decay]):
            return challenge.value
        
        solve_count = get_solve_count(challenge)
        if solve_count != 0:
            solve_count -= 1
        
        # Use CTFd's dynamic scoring formula
        # value = (((minimum - initial) / (decay ** 2)) * (solve_count ** 2)) + initial
        value = (((challenge.minimum - challenge.initial) / (challenge.decay ** 2)) * (solve_count ** 2)) + challenge.initial
        value = math.ceil(value)
        
        # Ensure value doesn't go below minimum
        if value < challenge.minimum:
            value = challenge.minimum
            
        return value

    @classmethod
    def read(cls, challenge):
        """
        This method is used to access the data of a challenge in a format processable by the front end.
        """
        challenge = cls.challenge_model.query.filter_by(id=challenge.id).first()
        data = {
            "id": challenge.id,
            "name": challenge.name,
            "value": challenge.value,
            "description": challenge.description,
            "category": challenge.category,
            "state": challenge.state,
            "max_attempts": challenge.max_attempts,
            "type": challenge.type,
            "type_data": {
                "id": cls.id,
                "name": cls.name,
                "templates": cls.templates,
                "scripts": cls.scripts,
            },
            "latitude": float(challenge.latitude) if challenge.latitude is not None else 0,
            "longitude": float(challenge.longitude) if challenge.longitude is not None else 0,
            "tolerance_radius": float(challenge.tolerance_radius) if challenge.tolerance_radius is not None else 10,
            "initial": challenge.initial,
            "minimum": challenge.minimum,
            "decay": challenge.decay,
        }
        return data

    @classmethod
    def create(cls, request):
        """
        This method is used to process the challenge creation request.
        """
        data = request.form or request.get_json()
        
        # Handle dynamic scoring fields - convert empty strings to None
        for field in ('initial', 'minimum', 'decay'):
            if field in data:
                value = data[field]
                if value == '' or value is None:
                    data[field] = None
                else:
                    try:
                        data[field] = int(value)
                    except (ValueError, TypeError):
                        data[field] = None

        challenge = cls.challenge_model(**data)
        db.session.add(challenge)
        db.session.commit()
        
        return challenge

    @classmethod
    def update(cls, challenge, request):
        """
        This method is used to update the information associated with a challenge.
        """
        data = request.form or request.get_json()
        
        GEO_UPDATABLE_FIELDS = {
            'name', 'description', 'category', 'value', 'max_attempts', 'state',
            'latitude', 'longitude', 'tolerance_radius', 'initial', 'minimum', 'decay',
        }
        for attr, value in data.items():
            if attr not in GEO_UPDATABLE_FIELDS:
                continue
            # Convert empty strings to None for numeric scoring fields
            if attr in ('initial', 'minimum', 'decay'):
                if value == '' or value is None:
                    value = None
                else:
                    try:
                        value = int(value)
                    except (ValueError, TypeError):
                        value = None
            if hasattr(challenge, attr):
                setattr(challenge, attr, value)

        db.session.commit()
        return challenge

    @classmethod
    def attempt(cls, challenge, request):
        """Handle submission attempt"""
        data = request.form or request.get_json()
        
        try:
            user_lat = float(data.get("latitude"))
            user_lon = float(data.get("longitude"))
        except (ValueError, TypeError):
            return ChallengeResponse(
                status="incorrect",
                message="Invalid coordinates submitted"
            )

        distance = cls.calculate_distance(
            float(challenge.latitude), float(challenge.longitude),
            user_lat, user_lon
        )

        if distance <= float(challenge.tolerance_radius):
            return ChallengeResponse(
                status="correct",
                message="Correct! You found the location!"
            )
        
        return ChallengeResponse(
            status="incorrect",
            message="Incorrect location. Try again!"
        )

    @classmethod
    def solve(cls, user, team, challenge, request):
        """Record solve with the challenge value"""
        data = request.form or request.get_json()
        submission = f"lat:{data['latitude']},lon:{data['longitude']}"


        solve = Solves(
            user_id=user.id,
            team_id=team.id if team else None,
            challenge_id=challenge.id,
            ip=get_ip(request),
            provided=submission
        )
        
        db.session.add(solve)
        
        # Calculate current value (supports dynamic scoring)
        current_value = cls.calculate_value(challenge)
        
        # If dynamic scoring is enabled, update the challenge value
        if all([challenge.initial, challenge.minimum, challenge.decay]):
            # Update the challenge's current value
            challenge.value = current_value
            db.session.add(challenge)
        
        db.session.commit()

    @classmethod
    def fail(cls, user, team, challenge, request):
        data = request.form or request.get_json()
        submission = f"lat:{data['latitude']},lon:{data['longitude']}"

        fail = Fails(
            user_id=user.id,
            team_id=team.id if team else None,
            challenge_id=challenge.id,
            ip=get_ip(request),
            provided=submission
        )
        
        db.session.add(fail)
        db.session.commit()

def load(app):
    
    upgrade(plugin_name="geo_challenges")

    # Create tables for the plugin
    app.db.create_all()
    
    # Register Leaflet globally
    register_plugin_stylesheet("/plugins/geo_challenges/assets/leaflet/leaflet.css")
    register_plugin_script("/plugins/geo_challenges/assets/leaflet/leaflet.js")
    register_admin_plugin_stylesheet("/plugins/geo_challenges/assets/leaflet/leaflet.css")
    register_admin_plugin_script("/plugins/geo_challenges/assets/leaflet/leaflet.js")
    
    # register geocontrol
    
    register_plugin_stylesheet("/plugins/geo_challenges/assets/geocoder/Control.Geocoder.css")
    register_plugin_script("/plugins/geo_challenges/assets/geocoder/Control.Geocoder.js")
    register_admin_plugin_stylesheet("/plugins/geo_challenges/assets/geocoder/Control.Geocoder.css")
    register_admin_plugin_script("/plugins/geo_challenges/assets/geocoder/Control.Geocoder.js")
    
    register_plugin_script("/plugins/geo_challenges/assets/view.js")

    # Ajout d'un script très simple pour les liens de coordonnées GPS
    @app.route('/plugins/geo_challenges/geo_link.js')
    def geo_link_script():
        return """
        document.addEventListener('DOMContentLoaded', function() {
            // Script minimal pour convertir les coordonnées GPS en liens
            setInterval(function() {
                var elements = document.querySelectorAll('pre, td');
                elements.forEach(function(el) {
                    if (el.hasAttribute('data-processed')) return;
                    
                    var text = el.innerText || '';
                    var match = text.match(/^lat:([-\\d.]+),lon:([-\\d.]+)$/);
                    
                    if (match) {
                        var lat = match[1];
                        var lon = match[2];
                        var url = 'https://www.openstreetmap.org/?mlat=' + lat + '&mlon=' + lon + '&zoom=15';
                        
                        // Créer un lien simple sans toucher au DOM complexe
                        var originalText = el.innerHTML;
                        var newHtml = '<a href="' + url + '" target="_blank">' + originalText + '</a>';
                        el.innerHTML = newHtml;
                        
                        // Marquer comme traité
                        el.setAttribute('data-processed', 'true');
                    }
                });
            }, 2000); // Exécuter toutes les 2 secondes
        });
        """

    # Enregistrer le script personnalisé
    register_plugin_script("/plugins/geo_challenges/geo_link.js")
    register_admin_plugin_script("/plugins/geo_challenges/geo_link.js")
    register_plugin_script("/plugins/geo_challenges/assets/i18n.js")
    
    # Register the plugin assets directory
    register_plugin_assets_directory(
        app, base_path="/plugins/geo_challenges/assets/"
    )
    
    # Register the challenge type's blueprint
    app.register_blueprint(GeoChallengeType.blueprint)
    
    # Register the challenge type
    CHALLENGE_CLASSES["geo"] = GeoChallengeType