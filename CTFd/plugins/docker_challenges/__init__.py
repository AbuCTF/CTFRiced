import traceback
import threading
import time
from datetime import datetime
import socket
import tempfile
import requests
import json
import hashlib
import random
import time
import threading
import traceback

from CTFd.plugins.challenges import BaseChallenge, CHALLENGE_CLASSES, get_chal_class
from CTFd.plugins.flags import get_flag_class
from CTFd.utils.user import get_ip
from CTFd.utils.uploads import delete_file
from CTFd.plugins import register_plugin_assets_directory, bypass_csrf_protection
from CTFd.schemas.tags import TagSchema
from CTFd.models import db, ma, Challenges, Tags, Users, Teams, Solves, Fails, Flags, Files, Hints, ChallengeFiles
from CTFd.utils.decorators import admins_only, authed_only, during_ctf_time_only, require_verified_emails
from CTFd.utils.decorators.visibility import check_challenge_visibility, check_score_visibility
from CTFd.utils.user import get_current_team
from CTFd.utils.user import get_current_user
from CTFd.utils.user import is_admin, authed
from CTFd.utils.config import is_teams_mode
from CTFd.api import CTFd_API_v1
from CTFd.api.v1.scoreboard import ScoreboardDetail
import CTFd.utils.scores
from CTFd.api.v1.challenges import ChallengeList, Challenge
from flask_restx import Namespace, Resource
from flask import request, Blueprint, jsonify, abort, render_template, url_for, redirect, session
# from flask_wtf import FlaskForm
from flask_wtf import FlaskForm
from wtforms import (
    FileField,
    HiddenField,
    PasswordField,
    RadioField,
    SelectField,
    StringField,
    TextAreaField,
    SelectMultipleField,
    BooleanField,
)
# from wtforms import TextField, SubmitField, BooleanField, HiddenField, FileField, SelectMultipleField
from wtforms.validators import DataRequired, ValidationError, InputRequired
from werkzeug.utils import secure_filename
import requests
import tempfile
from CTFd.utils.dates import unix_time
from datetime import datetime
import json
import hashlib
import random
from CTFd.plugins import register_admin_plugin_menu_bar

from CTFd.forms import BaseForm
from CTFd.forms.fields import SubmitField
from CTFd.utils.config import get_themes

from pathlib import Path

# Global variable for background cleanup thread
cleanup_thread = None


class DockerConfig(db.Model):
    """
	Docker Config Model. This model stores the config for docker API connections.
	Now supports multiple servers with optional domain mapping.
	"""
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column("name", db.String(128), nullable=False, index=True)  # Server name/identifier
    hostname = db.Column("hostname", db.String(128), index=True)
    domain = db.Column("domain", db.String(256), nullable=True, index=True)  # Optional subdomain
    tls_enabled = db.Column("tls_enabled", db.Boolean, default=False, index=True)
    ca_cert = db.Column("ca_cert", db.String(2200), index=True)
    client_cert = db.Column("client_cert", db.String(2000), index=True)
    client_key = db.Column("client_key", db.String(3300), index=True)
    repositories = db.Column("repositories", db.String(1024), index=True)
    is_active = db.Column("is_active", db.Boolean, default=True, index=True)  # Enable/disable server
    created_at = db.Column("created_at", db.DateTime, default=datetime.utcnow)
    last_status_check = db.Column("last_status_check", db.DateTime, nullable=True)
    status = db.Column("status", db.String(32), default="unknown")  # online, offline, error
    status_message = db.Column("status_message", db.String(512), nullable=True)


class DockerChallengeTracker(db.Model):
    """
	Docker Container Tracker. This model stores the users/teams active docker containers.
	"""
    id = db.Column(db.Integer, primary_key=True)
    team_id = db.Column("team_id", db.String(64), index=True)
    user_id = db.Column("user_id", db.String(64), index=True)
    docker_image = db.Column("docker_image", db.String(64), index=True)
    timestamp = db.Column("timestamp", db.Integer, index=True)
    revert_time = db.Column("revert_time", db.Integer, index=True)
    instance_id = db.Column("instance_id", db.String(128), index=True)
    ports = db.Column('ports', db.String(128), index=True)
    host = db.Column('host', db.String(128), index=True)
    challenge = db.Column('challenge', db.String(256), index=True)
    docker_config_id = db.Column("docker_config_id", db.Integer, db.ForeignKey('docker_config.id'), index=True)  # Which server was used
    
    # Relationship to get server info
    docker_config = db.relationship('DockerConfig', backref='active_containers')

class DockerConfigForm(FlaskForm):
    id = HiddenField()
    name = StringField(
        "Server Name", description="A friendly name for this Docker server (e.g., 'Main Server', 'PWN Server')"
    )
    hostname = StringField(
        "Docker Hostname", description="The Hostname/IP and Port of your Docker Server"
    )
    domain = StringField(
        "Domain (Optional)", description="Optional subdomain for this server (e.g., pwn.h7tex.com). Leave empty to show IP:port"
    )
    tls_enabled = RadioField('TLS Enabled?')
    ca_cert = FileField('CA Cert')
    client_cert = FileField('Client Cert')
    client_key = FileField('Client Key')
    repositories = SelectMultipleField('Repositories')
    is_active = BooleanField('Server Active', default=True)
    submit = SubmitField('Submit')


def define_docker_admin(app):
    admin_docker_config = Blueprint('admin_docker_config', __name__, template_folder='templates',
                                    static_folder='assets')

    @admin_docker_config.route("/admin/docker_config", methods=["GET"])
    @admins_only
    def docker_config_list():
        """List all Docker server configurations"""
        servers = DockerConfig.query.all()
        
        # Update status for all servers
        for server in servers:
            if not server.last_status_check or (datetime.utcnow() - server.last_status_check).seconds > 300:  # Update every 5 minutes
                update_server_status(server)
        
        return render_template("docker_config_list.html", servers=servers)

    @admin_docker_config.route("/admin/docker_config/add", methods=["GET", "POST"])
    @admins_only
    @bypass_csrf_protection
    def docker_config_add():
        """Add new Docker server configuration"""
        form = DockerConfigForm()
        
        if request.method == "POST":
            try:
                # Create new server config
                server = DockerConfig()
                server.name = request.form['name']
                server.hostname = request.form['hostname']
                server.domain = request.form.get('domain', '').strip() or None
                server.tls_enabled = request.form['tls_enabled'] == "True"
                server.is_active = 'is_active' in request.form
                
                # Handle certificate files
                try:
                    ca_cert = request.files['ca_cert'].stream.read()
                    if len(ca_cert) != 0: 
                        server.ca_cert = ca_cert.decode('utf-8')
                except Exception:
                    pass
                
                try:
                    client_cert = request.files['client_cert'].stream.read()
                    if len(client_cert) != 0: 
                        server.client_cert = client_cert.decode('utf-8')
                except Exception:
                    pass
                
                try:
                    client_key = request.files['client_key'].stream.read()
                    if len(client_key) != 0: 
                        server.client_key = client_key.decode('utf-8')
                except Exception:
                    pass
                
                if not server.tls_enabled:
                    server.ca_cert = None
                    server.client_cert = None
                    server.client_key = None
                
                # Handle repositories
                try:
                    server.repositories = ','.join(request.form.to_dict(flat=False)['repositories'])
                except Exception:
                    server.repositories = None
                
                db.session.add(server)
                db.session.commit()
                
                # Test the server connection
                update_server_status(server)
                
                return redirect(url_for('admin_docker_config.docker_config_list'))
                
            except Exception as e:
                print(f"Error adding server: {str(e)}")
                form.errors['general'] = [f"Error adding server: {str(e)}"]
        
        # Get repositories for form (try to get from any available server)
        try:
            repos = []
            servers = DockerConfig.query.filter_by(is_active=True).all()
            for server in servers:
                try:
                    server_repos = get_repositories(server)
                    repos.extend(server_repos)
                except Exception:
                    continue
            repos = list(set(repos))  # Remove duplicates
        except Exception:
            repos = []
        
        if len(repos) == 0:
            form.repositories.choices = [("ERROR", "No servers available or connection failed")]
        else:
            form.repositories.choices = [(d, d) for d in repos]
        
        return render_template("docker_config_form.html", form=form, action="Add", server=None)

    @admin_docker_config.route("/admin/docker_config/edit/<int:server_id>", methods=["GET", "POST"])
    @admins_only
    @bypass_csrf_protection
    def docker_config_edit(server_id):
        """Edit existing Docker server configuration"""
        server = DockerConfig.query.get_or_404(server_id)
        form = DockerConfigForm()
        
        if request.method == "POST":
            try:
                server.name = request.form['name']
                server.hostname = request.form['hostname']
                server.domain = request.form.get('domain', '').strip() or None
                server.tls_enabled = request.form['tls_enabled'] == "True"
                server.is_active = 'is_active' in request.form
                
                # Handle certificate files (only update if new files provided)
                try:
                    ca_cert = request.files['ca_cert'].stream.read()
                    if len(ca_cert) != 0: 
                        server.ca_cert = ca_cert.decode('utf-8')
                except Exception:
                    pass
                
                try:
                    client_cert = request.files['client_cert'].stream.read()
                    if len(client_cert) != 0: 
                        server.client_cert = client_cert.decode('utf-8')
                except Exception:
                    pass
                
                try:
                    client_key = request.files['client_key'].stream.read()
                    if len(client_key) != 0: 
                        server.client_key = client_key.decode('utf-8')
                except Exception:
                    pass
                
                if not server.tls_enabled:
                    server.ca_cert = None
                    server.client_cert = None
                    server.client_key = None
                
                # Handle repositories
                try:
                    server.repositories = ','.join(request.form.to_dict(flat=False)['repositories'])
                except Exception:
                    server.repositories = None
                
                db.session.commit()
                
                # Test the server connection
                update_server_status(server)
                
                return redirect(url_for('admin_docker_config.docker_config_list'))
                
            except Exception as e:
                print(f"Error updating server: {str(e)}")
                form.errors['general'] = [f"Error updating server: {str(e)}"]
        
        # Pre-populate form with existing data
        if request.method == "GET":
            form.name.data = server.name
            form.hostname.data = server.hostname
            form.domain.data = server.domain
            form.tls_enabled.data = "True" if server.tls_enabled else "False"
            form.is_active.data = server.is_active
        
        # Get repositories for this server
        try:
            repos = get_repositories(server)
        except Exception:
            repos = []
        
        if len(repos) == 0:
            form.repositories.choices = [("ERROR", "Failed to Connect to Docker")]
        else:
            form.repositories.choices = [(d, d) for d in repos]
        
        # Set selected repositories
        try:
            if server.repositories:
                selected_repos = server.repositories.split(',')
                form.repositories.data = selected_repos
        except Exception:
            pass
        
        return render_template("docker_config_form.html", form=form, action="Edit", server=server)

    @admin_docker_config.route("/admin/docker_config/delete/<int:server_id>", methods=["POST"])
    @admins_only
    @bypass_csrf_protection
    def docker_config_delete(server_id):
        """Delete Docker server configuration"""
        try:
            server = DockerConfig.query.get_or_404(server_id)
            
            # Check if server has active containers
            active_containers = DockerChallengeTracker.query.filter_by(docker_config_id=server_id).count()
            if active_containers > 0:
                return jsonify({"success": False, "message": f"Cannot delete server with {active_containers} active containers"}), 400
            
            db.session.delete(server)
            db.session.commit()
            
            return jsonify({"success": True, "message": "Server deleted successfully"})
        except Exception as e:
            return jsonify({"success": False, "message": f"Error deleting server: {str(e)}"}), 500

    @admin_docker_config.route("/admin/docker_config/test/<int:server_id>", methods=["POST"])
    @admins_only
    @bypass_csrf_protection
    def docker_config_test(server_id):
        """Test Docker server connection"""
        try:
            server = DockerConfig.query.get_or_404(server_id)
            is_healthy, message = update_server_status(server)
            
            return jsonify({
                "success": is_healthy,
                "message": message,
                "status": server.status
            })
        except Exception as e:
            return jsonify({"success": False, "message": f"Error testing server: {str(e)}"}), 500

    app.register_blueprint(admin_docker_config)


def define_docker_status(app):
    docker_status = Blueprint('docker_status', __name__, template_folder='templates',
                              static_folder='assets')

    @docker_status.route("/admin/docker_status", methods=["GET", "POST"])
    @admins_only
    def docker_admin():
        # Get all servers and their status
        servers = DockerConfig.query.all()
        
        # Update server statuses if needed
        for server in servers:
            if not server.last_status_check or (datetime.utcnow() - server.last_status_check).seconds > 300:
                update_server_status(server)
        
        # Get all active containers with server information
        docker_tracker = DockerChallengeTracker.query.all()
        
        # Enhance tracker data with user/team names and server info
        for i in docker_tracker:
            if is_teams_mode():
                if i.team_id is not None:
                    name = Teams.query.filter_by(id=i.team_id).first()
                    i.team_id = name.name if name else f"Unknown Team ({i.team_id})"
                else:
                    i.team_id = "Unknown Team (None)"
            else:
                if i.user_id is not None:
                    name = Users.query.filter_by(id=i.user_id).first()
                    i.user_id = name.name if name else f"Unknown User ({i.user_id})"
                else:
                    i.user_id = "Unknown User (None)"
            
            # Add server name for display
            if i.docker_config:
                i.server_name = i.docker_config.name
                i.server_domain = i.docker_config.domain
            else:
                i.server_name = "Unknown Server"
                i.server_domain = None
        
        return render_template("admin_docker_status.html", 
                             dockers=docker_tracker, 
                             servers=servers,
                             now=datetime.utcnow())

    app.register_blueprint(docker_status)


kill_container = Namespace("nuke", description='Endpoint to nuke containers')


@kill_container.route("", methods=['POST', 'GET'])
class KillContainerAPI(Resource):
    @admins_only
    def get(self):
        try:
            container = request.args.get('container')
            full = request.args.get('all')
            
            docker_tracker = DockerChallengeTracker.query.all()
            
            if full == "true":
                for c in docker_tracker:
                    try:
                        if c.docker_config:
                            delete_container(c.docker_config, c.instance_id)
                        # Delete the tracker record individually
                        tracker_to_delete = DockerChallengeTracker.query.filter_by(instance_id=c.instance_id).first()
                        if tracker_to_delete:
                            db.session.delete(tracker_to_delete)
                        db.session.commit()
                    except Exception as e:
                        print(f"Error deleting container {c.instance_id}: {str(e)}")
                        continue

            elif container != 'null' and container in [c.instance_id for c in docker_tracker]:
                try:
                    container_to_delete = DockerChallengeTracker.query.filter_by(instance_id=container).first()
                    if container_to_delete and container_to_delete.docker_config:
                        delete_container(container_to_delete.docker_config, container)
                    # Delete the tracker record individually
                    tracker_to_delete = DockerChallengeTracker.query.filter_by(instance_id=container).first()
                    if tracker_to_delete:
                        db.session.delete(tracker_to_delete)
                    db.session.commit()
                except Exception as e:
                    print(f"Error deleting container {container}: {str(e)}")
                    return {"success": False, "message": f"Error deleting container: {str(e)}"}, 500

            else:
                return {"success": False, "message": "Invalid container specified"}, 400
                
            return {"success": True, "message": "Container(s) deleted successfully"}
            
        except Exception as e:
            print(f"Error in nuke endpoint: {str(e)}")
            traceback.print_exc()
            return {"success": False, "message": f"Internal server error: {str(e)}"}, 500


def check_server_health(docker_config):
    """
    Check if a Docker server is healthy and update its status
    Returns: (is_healthy: bool, status_message: str)
    """
    try:
        # Test Docker API connection
        r = do_request(docker_config, '/version', timeout=10)
        
        if r is None:
            return False, "Connection timeout or failed"
        
        if not hasattr(r, 'status_code') or r.status_code != 200:
            return False, f"Docker API returned status {r.status_code if hasattr(r, 'status_code') else 'unknown'}"
        
        # If we have a domain, try to validate it resolves to the same IP
        if docker_config.domain:
            try:
                import socket
                # Extract IP from hostname (remove port if present)
                server_ip = docker_config.hostname.split(':')[0]
                domain_ip = socket.gethostbyname(docker_config.domain.split(':')[0])
                
                if server_ip != domain_ip and server_ip != '127.0.0.1' and server_ip != 'localhost':
                    return True, f"Warning: Domain {docker_config.domain} resolves to {domain_ip}, but server is at {server_ip}"
            except socket.gaierror:
                return True, f"Warning: Domain {docker_config.domain} does not resolve"
            except Exception as e:
                return True, f"Warning: Could not validate domain: {str(e)}"
        
        return True, "Online"
        
    except Exception as e:
        return False, f"Error: {str(e)}"


def update_server_status(docker_config):
    """
    Update a server's status in the database
    """
    try:
        is_healthy, message = check_server_health(docker_config)
        
        docker_config.last_status_check = datetime.utcnow()
        docker_config.status = "online" if is_healthy else "error"
        docker_config.status_message = message
        
        db.session.commit()
        
        return is_healthy, message
    except Exception as e:
        print(f"Error updating server status: {str(e)}")
        return False, f"Update failed: {str(e)}"


def get_best_server_for_image(image_name):
    """
    Find the best available server that has the requested image
    Returns the DockerConfig object or None
    """
    try:
        # Get all active servers
        servers = DockerConfig.query.filter_by(is_active=True).all()
        
        for server in servers:
            try:
                # Check if server has the image
                repositories = get_repositories(server, tags=True)
                if image_name in repositories:
                    # Check if server is healthy
                    is_healthy, _ = check_server_health(server)
                    if is_healthy:
                        return server
            except Exception as e:
                print(f"Error checking server {server.name}: {str(e)}")
                continue
        
        return None
    except Exception as e:
        print(f"Error finding best server: {str(e)}")
        return None


def do_request(docker, url, headers=None, method='GET', timeout=30):
    tls = docker.tls_enabled
    prefix = 'https' if tls else 'http'
    host = docker.hostname
    URL_TEMPLATE = '%s://%s' % (prefix, host)
    
    try:
        if tls:
            cert, verify = get_client_cert(docker)
            if method == 'GET':
                r = requests.get(url=f"%s{url}" % URL_TEMPLATE, cert=cert, verify=verify, headers=headers, timeout=timeout)
            elif method == 'DELETE':
                r = requests.delete(url=f"%s{url}" % URL_TEMPLATE, cert=cert, verify=verify, headers=headers, timeout=timeout)
            elif method == 'POST':
                r = requests.post(url=f"%s{url}" % URL_TEMPLATE, cert=cert, verify=verify, headers=headers, timeout=timeout)
            # Clean up the cert files:
            for file_path in [*cert, verify]:
                if file_path:
                    Path(file_path).unlink(missing_ok=True)
        else:
            if method == 'GET':
                r = requests.get(url=f"%s{url}" % URL_TEMPLATE, headers=headers, timeout=timeout)
            elif method == 'DELETE':
                r = requests.delete(url=f"%s{url}" % URL_TEMPLATE, headers=headers, timeout=timeout)
            elif method == 'POST':
                r = requests.post(url=f"%s{url}" % URL_TEMPLATE, headers=headers, timeout=timeout)
        return r
    except requests.exceptions.Timeout:
        print(f"Timeout making request to {URL_TEMPLATE}{url}")
        return None
    except requests.exceptions.ConnectionError:
        print(f"Connection error making request to {URL_TEMPLATE}{url}")
        return None
    except Exception as e:
        print(f"Error making request to {URL_TEMPLATE}{url}: {str(e)}")
        return None


def get_client_cert(docker):
    # this can be done more efficiently, but works for now.
    try:
        ca = docker.ca_cert
        client = docker.client_cert
        ckey = docker.client_key
        
        # Create temporary files with proper cleanup
        ca_file = tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.pem')
        ca_file.write(ca)
        ca_file.close()
        
        client_file = tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.pem')
        client_file.write(client)
        client_file.close()
        
        key_file = tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.pem')
        key_file.write(ckey)
        key_file.close()
        
        CERT = (client_file.name, key_file.name)
    except Exception:
        CERT = None
    return CERT, ca_file.name if 'ca_file' in locals() else None


# For the Docker Config Page. Gets the Current Repositories available on the Docker Server.
def get_repositories(docker, tags=False, repos=False):
    try:
        r = do_request(docker, '/images/json?all=1')
        if r is None:
            print("ERROR: do_request returned None for /images/json")
            return []
        
        if not hasattr(r, 'status_code') or r.status_code != 200:
            print(f"ERROR: Docker API returned status {r.status_code if hasattr(r, 'status_code') else 'unknown'}")
            return []
        
        result = list()
        try:
            images = r.json()
            for i in images:
                if not i['RepoTags'] == []:
                    if not i['RepoTags'][0].split(':')[0] == '<none>':
                        image_name = i['RepoTags'][0].split(':')[0]
                        if repos:
                            # repos is a list of allowed repository names
                            if image_name not in repos:
                                continue
                        if not tags:
                            result.append(image_name)
                        else:
                            result.append(i['RepoTags'][0])
        except Exception as e:
            print(f"ERROR: Failed to parse Docker images response: {str(e)}")
            return []
        
        return list(set(result))
    except Exception as e:
        print(f"ERROR in get_repositories(): {str(e)}")
        import traceback
        traceback.print_exc()
        return []


def get_unavailable_ports(docker):
    try:
        r = do_request(docker, '/containers/json?all=1')
        if r is None:
            print("ERROR: do_request returned None for /containers/json")
            return []
        
        if not hasattr(r, 'status_code') or r.status_code != 200:
            print(f"ERROR: Docker API returned status {r.status_code if hasattr(r, 'status_code') else 'unknown'}")
            return []
        
        result = list()
        try:
            containers = r.json()
            for i in containers:
                if not i['Ports'] == []:
                    for p in i['Ports']:
                        if 'PublicPort' in p:
                            result.append(p['PublicPort'])
        except Exception as e:
            print(f"ERROR: Failed to parse Docker containers response: {str(e)}")
            return []
        
        return result
    except Exception as e:
        print(f"ERROR in get_unavailable_ports(): {str(e)}")
        import traceback
        traceback.print_exc()
        return []


def get_required_ports(docker, image):
    try:
        r = do_request(docker, f'/images/{image}/json?all=1')
        if r is None:
            print(f"ERROR: do_request returned None for /images/{image}/json")
            return []
        
        if not hasattr(r, 'status_code') or r.status_code != 200:
            print(f"ERROR: Docker API returned status {r.status_code if hasattr(r, 'status_code') else 'unknown'}")
            return []
        
        try:
            image_info = r.json()
            if 'Config' in image_info and 'ExposedPorts' in image_info['Config'] and image_info['Config']['ExposedPorts']:
                result = image_info['Config']['ExposedPorts'].keys()
                return result
            else:
                print(f"WARNING: No exposed ports found for image {image}")
                return []
        except Exception as e:
            print(f"ERROR: Failed to parse image info response: {str(e)}")
            return []
    except Exception as e:
        print(f"ERROR in get_required_ports(): {str(e)}")
        import traceback
        traceback.print_exc()
        return []


def create_container(docker, image, team, portbl):
    try:
        tls = docker.tls_enabled
        CERT = None
        if not tls:
            prefix = 'http'
        else:
            prefix = 'https'
        host = docker.hostname
        URL_TEMPLATE = '%s://%s' % (prefix, host)
        
        try:
            needed_ports = get_required_ports(docker, image)
        except Exception as e:
            print(f"ERROR: Failed to get required ports: {str(e)}")
            raise Exception(f"Failed to get required ports for image {image}")
        
        team = hashlib.md5(team.encode("utf-8")).hexdigest()[:10]
        # Sanitize image name to prevent injection
        image_safe = image.replace('/', '_').replace(':', '_')
        container_name = "%s_%s" % (image_safe, team)
        
        # Check if container with this name already exists and remove it
        try:
            existing_containers_response = do_request(docker, '/containers/json?all=1')
            if existing_containers_response and hasattr(existing_containers_response, 'status_code') and existing_containers_response.status_code == 200:
                containers = existing_containers_response.json()
                for container in containers:
                    for name in container.get('Names', []):
                        if name.lstrip('/') == container_name:
                            try:
                                # Stop the container first
                                stop_response = do_request(docker, f'/containers/{container["Id"]}/stop', method='POST')
                                
                                # Remove the container
                                remove_response = do_request(docker, f'/containers/{container["Id"]}?force=true', method='DELETE')
                                
                                # Also remove from database if it exists
                                try:
                                    DockerChallengeTracker.query.filter_by(instance_id=container['Id']).delete()
                                    db.session.commit()
                                except Exception as db_e:
                                    print(f"Warning: Error removing from database: {str(db_e)}")
                                
                            except Exception as rm_e:
                                print(f"Warning: Error removing existing container: {str(rm_e)}")
                                # Continue anyway, the create might still work
                            break
        except Exception as e:
            print(f"Warning: Error checking for existing containers: {str(e)}")
            # Continue anyway
        
        assigned_ports = dict()
        for i in needed_ports:
            attempts = 0
            while attempts < 100:  # Prevent infinite loop
                assigned_port = random.choice(range(30000, 60000))
                if assigned_port not in portbl:
                    assigned_ports['%s/tcp' % assigned_port] = {}
                    break
                attempts += 1
            if attempts >= 100:
                raise Exception("Could not find available port after 100 attempts")
        
        ports = dict()
        bindings = dict()
        tmp_ports = list(assigned_ports.keys())
        for i in needed_ports:
            ports[i] = {}
            bindings[i] = [{"HostPort": tmp_ports.pop()}]
        
        headers = {'Content-Type': "application/json"}
        data = json.dumps({"Image": image, "ExposedPorts": ports, "HostConfig": {"PortBindings": bindings}})
        
        if tls:
            cert, verify = get_client_cert(docker)
            r = requests.post(url="%s/containers/create?name=%s" % (URL_TEMPLATE, container_name), cert=cert,
                          verify=verify, data=data, headers=headers)
            if r.status_code not in [200, 201]:
                print(f"ERROR: Container creation failed with status {r.status_code}: {r.text}")
                raise Exception(f"Container creation failed: {r.text}")
                
            result = r.json()
            
            s = requests.post(url="%s/containers/%s/start" % (URL_TEMPLATE, result['Id']), cert=cert, verify=verify,
                              headers=headers)
            if s.status_code not in [200, 204]:
                print(f"ERROR: Container start failed with status {s.status_code}: {s.text}")
                raise Exception(f"Container start failed: {s.text}")
                
            # Clean up the cert files:
            for file_path in [*cert, verify]:
                if file_path:
                    Path(file_path).unlink(missing_ok=True)
        else:
            r = requests.post(url="%s/containers/create?name=%s" % (URL_TEMPLATE, container_name),
                              data=data, headers=headers)
            
            if r.status_code not in [200, 201]:
                print(f"ERROR: Container creation failed with status {r.status_code}: {r.text}")
                raise Exception(f"Container creation failed: {r.text}")
                
            result = r.json()
            
            # name conflicts are not handled properly
            s = requests.post(url="%s/containers/%s/start" % (URL_TEMPLATE, result['Id']), headers=headers)
            if s.status_code not in [200, 204]:
                print(f"ERROR: Container start failed with status {s.status_code}: {s.text}")
                raise Exception(f"Container start failed: {s.text}")
        
        return result, data, docker  # Return the docker config used
    except Exception as e:
        print(f"ERROR in create_container(): {str(e)}")
        import traceback
        traceback.print_exc()
        raise


def delete_container(docker, instance_id):
    """
    Delete a Docker container by instance ID
    """
    try:
        if not instance_id:
            return False
            
        headers = {'Content-Type': "application/json"}
        response = do_request(docker, f'/containers/{instance_id}?force=true', headers=headers, method='DELETE')
        
        if response is None:
            print(f"Warning: Failed to connect to Docker API for container {instance_id}")
            return False
            
        if hasattr(response, 'status_code') and response.status_code not in [200, 204, 404]:
            print(f"Warning: Container deletion returned status code {response.status_code}")
            return False
            
        return True
    except Exception as e:
        print(f"Error deleting container {instance_id}: {str(e)}")
        return False


class DockerChallengeType(BaseChallenge):
    id = "docker"
    name = "docker"
    templates = {
        'create': '/plugins/docker_challenges/assets/create.html',
        'update': '/plugins/docker_challenges/assets/update.html',
        'view': '/plugins/docker_challenges/assets/view.html',
    }
    scripts = {
        'create': '/plugins/docker_challenges/assets/create.js',
        'update': '/plugins/docker_challenges/assets/update.js',
        'view': '/plugins/docker_challenges/assets/view.js?v=20250715185300',
    }
    route = '/plugins/docker_challenges/assets'
    blueprint = Blueprint('docker_challenges', __name__, template_folder='templates', static_folder='assets')

    @staticmethod
    def update(challenge, request):
        """
		This method is used to update the information associated with a challenge. This should be kept strictly to the
		Challenges table and any child tables.

		:param challenge:
		:param request:
		:return:
		"""
        data = request.form or request.get_json()
        for attr, value in data.items():
            setattr(challenge, attr, value)

        db.session.commit()
        return challenge

    @staticmethod
    def delete(challenge):
        """
		This method is used to delete the resources used by a challenge.
		NOTE: Will need to kill all containers here

		:param challenge:
		:return:
		"""
        Fails.query.filter_by(challenge_id=challenge.id).delete()
        Solves.query.filter_by(challenge_id=challenge.id).delete()
        Flags.query.filter_by(challenge_id=challenge.id).delete()
        files = ChallengeFiles.query.filter_by(challenge_id=challenge.id).all()
        for f in files:
            delete_file(f.id)
        ChallengeFiles.query.filter_by(challenge_id=challenge.id).delete()
        Tags.query.filter_by(challenge_id=challenge.id).delete()
        Hints.query.filter_by(challenge_id=challenge.id).delete()
        DockerChallenge.query.filter_by(id=challenge.id).delete()
        Challenges.query.filter_by(id=challenge.id).delete()
        db.session.commit()

    @staticmethod
    def read(challenge):
        """
		This method is in used to access the data of a challenge in a format processable by the front end.

		:param challenge:
		:return: Challenge object, data dictionary to be returned to the user
		"""
        challenge = DockerChallenge.query.filter_by(id=challenge.id).first()
        data = {
            'id': challenge.id,
            'name': challenge.name,
            'value': challenge.value,
            'docker_image': challenge.docker_image,
            'docker_config_id': challenge.docker_config_id,
            'server_name': challenge.docker_config.name if challenge.docker_config else 'Unknown Server',
            'description': challenge.description,
            'category': challenge.category,
            'state': challenge.state,
            'max_attempts': challenge.max_attempts,
            'type': challenge.type,
            'type_data': {
                'id': DockerChallengeType.id,
                'name': DockerChallengeType.name,
                'templates': DockerChallengeType.templates,
                'scripts': DockerChallengeType.scripts,
            }
        }
        return data

    @staticmethod
    def create(request):
        """
		This method is used to process the challenge creation request.
		Now handles server selection for multi-server setup.

		:param request:
		:return:
		"""
        data = request.form or request.get_json()
        
        # Handle the new format: "ServerName | ImageName"
        docker_image_selection = data.get('docker_image', '')
        
        if ' | ' in docker_image_selection:
            # New format
            server_name, image_name = docker_image_selection.split(' | ', 1)
            server = DockerConfig.query.filter_by(name=server_name, is_active=True).first()
            if not server:
                raise Exception(f"Server '{server_name}' not found or inactive")
            
            challenge_data = dict(data)
            challenge_data['docker_image'] = image_name
            challenge_data['docker_config_id'] = server.id
        else:
            # Backward compatibility: try to find any server that has this image
            image_name = docker_image_selection
            server = get_best_server_for_image(image_name)
            if not server:
                # Fallback to first server for backward compatibility
                server = DockerConfig.query.filter_by(is_active=True).first()
                if not server:
                    raise Exception("No active Docker servers available")
            
            challenge_data = dict(data)
            challenge_data['docker_image'] = image_name
            challenge_data['docker_config_id'] = server.id
        
        challenge = DockerChallenge(**challenge_data)
        db.session.add(challenge)
        db.session.commit()
        return challenge

    @staticmethod
    def attempt(challenge, request):
        """
		This method is used to check whether a given input is right or wrong. It does not make any changes and should
		return a boolean for correctness and a string to be shown to the user. It is also in charge of parsing the
		user's input from the request itself.

		:param challenge: The Challenge object from the database
		:param request: The request the user submitted
		:return: (boolean, string)
		"""

        data = request.form or request.get_json()
        print(request.get_json())
        print(data)
        submission = data["submission"].strip()
        flags = Flags.query.filter_by(challenge_id=challenge.id).all()
        for flag in flags:
            if get_flag_class(flag.type).compare(flag, submission):
                return True, "Correct"
        return False, "Incorrect"

    @staticmethod
    def solve(user, team, challenge, request):
        """
		This method is used to insert Solves into the database in order to mark a challenge as solved.

		:param team: The Team object from the database
		:param chal: The Challenge object from the database
		:param request: The request the user submitted
		:return:
		"""
        data = request.form or request.get_json()
        submission = data["submission"].strip()
        
        try:
            if is_teams_mode():
                docker_containers = DockerChallengeTracker.query.filter_by(
                    docker_image=challenge.docker_image).filter_by(team_id=team.id).first()
            else:
                docker_containers = DockerChallengeTracker.query.filter_by(
                    docker_image=challenge.docker_image).filter_by(user_id=user.id).first()
            
            if docker_containers and docker_containers.docker_config:
                delete_container(docker_containers.docker_config, docker_containers.instance_id)
                DockerChallengeTracker.query.filter_by(instance_id=docker_containers.instance_id).delete()
                db.session.commit()
        except Exception as e:
            print(f"Warning: Error cleaning up container on solve: {str(e)}")
            # Continue anyway
        
        solve = Solves(
            user_id=user.id,
            team_id=team.id if team else None,
            challenge_id=challenge.id,
            ip=get_ip(req=request),
            provided=submission,
        )
        db.session.add(solve)
        db.session.commit()
        # trying if this solces the detached instance error...
        #db.session.close()

    @staticmethod
    def fail(user, team, challenge, request):
        """
		This method is used to insert Fails into the database in order to mark an answer incorrect.

		:param team: The Team object from the database
		:param chal: The Challenge object from the database
		:param request: The request the user submitted
		:return:
		"""
        data = request.form or request.get_json()
        submission = data["submission"].strip()
        wrong = Fails(
            user_id=user.id,
            team_id=team.id if team else None,
            challenge_id=challenge.id,
            ip=get_ip(request),
            provided=submission,
        )
        db.session.add(wrong)
        db.session.commit()
        #db.session.close()


class DockerChallenge(Challenges):
    __mapper_args__ = {'polymorphic_identity': 'docker'}
    id = db.Column(None, db.ForeignKey('challenges.id'), primary_key=True)
    docker_image = db.Column(db.String(128), index=True)
    docker_config_id = db.Column("docker_config_id", db.Integer, db.ForeignKey('docker_config.id'), index=True)  # Which server to use
    
    # Relationship to get server info
    docker_config = db.relationship('DockerConfig')


# API
container_namespace = Namespace("container", description='Endpoint to interact with containers')


@container_namespace.route("", methods=['POST', 'GET'])
class ContainerAPI(Resource):
    @authed_only
    # I wish this was Post... Issues with API/CSRF and whatnot. Open to a Issue solving this.
    def get(self):
        try:
            container = request.args.get('name')
            if not container:
                return abort(403, "No container specified")
            
            # Basic input validation
            if not isinstance(container, str) or len(container) > 256:
                return abort(400, "Invalid container name")
                
            challenge = request.args.get('challenge')
            if not challenge:
                return abort(403, "No challenge name specified")
                
            # Basic input validation
            if not isinstance(challenge, str) or len(challenge) > 256:
                return abort(400, "Invalid challenge name")
            
            # Find the best server for this container image
            docker = get_best_server_for_image(container)
            if not docker:
                return abort(500, f"No available Docker server found for image: {container}")
            
            # Check if container exists in repository
            try:
                repositories = get_repositories(docker, tags=True)
                if container not in repositories:
                    return abort(403, f"Container {container} not present in the repository on server {docker.name}.")
            except Exception as e:
                print(f"Error getting repositories: {str(e)}")
                import traceback
                traceback.print_exc()
                return abort(500, "Failed to get repository list")
            
            # Get current session
            try:
                if is_teams_mode():
                    session = get_current_team()
                else:
                    session = get_current_user()
                    
                if not session:
                    return abort(403, "No valid session")
            except Exception as e:
                print(f"Error getting session: {str(e)}")
                import traceback
                traceback.print_exc()
                return abort(500, "Failed to get user session")
            
            containers = DockerChallengeTracker.query.all()
            
            # Clean up expired containers first (older than 2 hours)
            try:
                containers_to_remove = []
                current_time = unix_time(datetime.utcnow())
                
                for i in containers:
                    container_age = current_time - int(i.timestamp)
                    if is_teams_mode():
                        if i.team_id is not None and int(session.id) == int(i.team_id) and container_age >= 7200:
                            try:
                                if i.docker_config:
                                    delete_container(i.docker_config, i.instance_id)
                                DockerChallengeTracker.query.filter_by(instance_id=i.instance_id).delete()
                                db.session.commit()
                            except Exception as e:
                                print(f"Error removing old team container: {str(e)}")
                    else:
                        if i.user_id is not None and int(session.id) == int(i.user_id) and container_age >= 7200:
                            try:
                                if i.docker_config:
                                    delete_container(i.docker_config, i.instance_id)
                                DockerChallengeTracker.query.filter_by(instance_id=i.instance_id).delete()
                                db.session.commit()
                            except Exception as e:
                                print(f"Error removing old user container: {str(e)}")
            except Exception as e:
                print(f"Error during old container cleanup: {str(e)}")
                import traceback
                traceback.print_exc()
            
            # Check for existing container for this specific image
            # Also implement a basic rate limiting (minimum 30 seconds between requests)
            try:
                if is_teams_mode():
                    check = DockerChallengeTracker.query.filter_by(team_id=session.id).filter_by(docker_image=container).first()
                else:
                    check = DockerChallengeTracker.query.filter_by(user_id=session.id).filter_by(docker_image=container).first()
                
                # Check if user is making requests too frequently
                if check and (unix_time(datetime.utcnow()) - int(check.timestamp)) < 30:
                    return abort(429, "Rate limit exceeded. Please wait at least 30 seconds between requests.")
                    
            except Exception as e:
                print(f"Error checking existing container: {str(e)}")
                import traceback
                traceback.print_exc()
                check = None
            
            # If this container is already created, we don't need another one.
            if check != None and not (unix_time(datetime.utcnow()) - int(check.timestamp)) >= 300:
                return abort(403,"To prevent abuse, dockers can be reverted and stopped after 5 minutes of creation.")
            # Delete when requested
            elif check != None and request.args.get('stopcontainer'):
                try:
                    if check.docker_config:
                        delete_container(check.docker_config, check.instance_id)
                    if is_teams_mode():
                        DockerChallengeTracker.query.filter_by(team_id=session.id).filter_by(docker_image=container).delete()
                    else:
                        DockerChallengeTracker.query.filter_by(user_id=session.id).filter_by(docker_image=container).delete()
                    db.session.commit()
                    return {"result": "Container stopped"}
                except Exception as e:
                    print(f"Error stopping container: {str(e)}")
                    import traceback
                    traceback.print_exc()
                    return abort(500, "Failed to stop container")
            # The exception would be if we are reverting a box. So we'll delete it if it exists and has been around for more than 5 minutes.
            elif check != None:
                try:
                    if check.docker_config:
                        delete_container(check.docker_config, check.instance_id)
                    if is_teams_mode():
                        DockerChallengeTracker.query.filter_by(team_id=session.id).filter_by(docker_image=container).delete()
                    else:
                        DockerChallengeTracker.query.filter_by(user_id=session.id).filter_by(docker_image=container).delete()
                    db.session.commit()
                except Exception as e:
                    print(f"Error deleting existing container: {str(e)}")
                    import traceback
                    traceback.print_exc()
            
            # Check if a container is already running for this user. We need to recheck the DB first
            # Also clean up any expired containers (older than 5 minutes)
            containers = DockerChallengeTracker.query.all()
            containers_to_remove = []
            
            for i in containers:
                # Check if container has expired (older than 5 minutes = 300 seconds)
                current_time = unix_time(datetime.utcnow())
                container_age = current_time - int(i.timestamp)
                
                if container_age >= 300:
                    try:
                        if i.docker_config:
                            delete_container(i.docker_config, i.instance_id)
                        containers_to_remove.append(i)
                    except Exception as e:
                        print(f"Error deleting expired container {i.instance_id}: {str(e)}")
                        # Only remove from DB if Docker deletion was successful
                        continue
                    continue
                
                # Check if user already has a running container (not expired)
                if is_teams_mode():
                    # In teams mode, check team_id
                    if i.team_id is not None and int(session.id) == int(i.team_id):
                        return {"message": f"Another container is already running for challenge:<br><i><b>{i.challenge}</b></i>.<br>Please stop this first.<br>You can only run one container."}, 403
                else:
                    # In user mode, check user_id
                    if i.user_id is not None and int(session.id) == int(i.user_id):
                        return {"message": f"Another container is already running for challenge:<br><i><b>{i.challenge}</b></i>.<br>Please stop this first.<br>You can only run one container."}, 403
            
            # Remove expired containers from database
            for container_obj in containers_to_remove:
                try:
                    DockerChallengeTracker.query.filter_by(instance_id=container_obj.instance_id).delete()
                    db.session.commit()
                except Exception as e:
                    print(f"Error removing expired container from DB: {str(e)}")

            # Get ports and create container
            try:
                portsbl = get_unavailable_ports(docker)
                
                create = create_container(docker, container, session.name, portsbl)
                
                ports = json.loads(create[1])['HostConfig']['PortBindings'].values()
                
                # Determine what host/domain to show to user
                display_host = docker.domain if docker.domain else str(docker.hostname).split(':')[0]
                
                entry = DockerChallengeTracker(
                    team_id=session.id if is_teams_mode() else None,
                    user_id=session.id if not is_teams_mode() else None,
                    docker_image=container,
                    timestamp=unix_time(datetime.utcnow()),
                    revert_time=unix_time(datetime.utcnow()) + 300,
                    instance_id=create[0]['Id'],
                    ports=','.join([p[0]['HostPort'] for p in ports]),
                    host=display_host,
                    challenge=challenge,
                    docker_config_id=docker.id
                )
                db.session.add(entry)
                db.session.commit()
                return {"result": "Container created successfully"}
            except Exception as e:
                print(f"Error creating container: {str(e)}")
                import traceback
                traceback.print_exc()
                return abort(500, f"Failed to create container: {str(e)}")
        
        except Exception as e:
            print(f"ERROR in ContainerAPI.get(): {str(e)}")
            import traceback
            traceback.print_exc()
            return abort(500, f"Internal server error: {str(e)}")


active_docker_namespace = Namespace("docker", description='Endpoint to retrieve User Docker Image Status')


@active_docker_namespace.route("", methods=['POST', 'GET'])
class DockerStatus(Resource):
    """
	The Purpose of this API is to retrieve a public JSON string of all docker containers
	in use by the current team/user.
	"""

    @authed_only
    def get(self):
        if is_teams_mode():
            session = get_current_team()
            tracker = DockerChallengeTracker.query.filter_by(team_id=session.id)
        else:
            session = get_current_user()
            tracker = DockerChallengeTracker.query.filter_by(user_id=session.id)
        
        # First, clean up ALL expired containers globally (not just for current user)
        all_containers = DockerChallengeTracker.query.all()
        global_containers_to_remove = []
        
        for container in all_containers:
            container_age = unix_time(datetime.utcnow()) - int(container.timestamp)
            if container_age >= 300:  # 5 minutes
                try:
                    if container.docker_config:
                        delete_container(container.docker_config, container.instance_id)
                    global_containers_to_remove.append(container)
                except Exception as e:
                    print(f"Error deleting expired container {container.instance_id}: {str(e)}")
                    # Still remove from DB even if Docker deletion fails
                    global_containers_to_remove.append(container)
        
        # Remove expired containers from database
        for container in global_containers_to_remove:
            try:
                DockerChallengeTracker.query.filter_by(instance_id=container.instance_id).delete()
                db.session.commit()
            except Exception as e:
                print(f"Error removing expired container from DB: {str(e)}")
                db.session.rollback()
        
        # Now get current user/team containers (after cleanup)
        if is_teams_mode():
            tracker = DockerChallengeTracker.query.filter_by(team_id=session.id)
        else:
            tracker = DockerChallengeTracker.query.filter_by(user_id=session.id)
        # Now get the user's current containers (after cleanup)
        data = list()
        containers_to_remove = []
        
        for i in tracker:
            # Check if container has expired (older than 5 minutes = 300 seconds)
            if (unix_time(datetime.utcnow()) - int(i.timestamp)) >= 300:
                try:
                    if i.docker_config:
                        delete_container(i.docker_config, i.instance_id)
                    containers_to_remove.append(i)
                except Exception as e:
                    print(f"Error deleting expired container {i.instance_id}: {str(e)}")
                    # Only remove from DB if Docker deletion was successful
                    continue
                continue
            
            # Determine display host (domain or IP)
            display_host = i.host  # This is already set correctly in container creation
            if i.docker_config and i.docker_config.domain:
                display_host = i.docker_config.domain.split(':')[0]
                
            data.append({
                'id': i.id,
                'team_id': i.team_id,
                'user_id': i.user_id,
                'docker_image': i.docker_image,
                'timestamp': i.timestamp,
                'revert_time': i.revert_time,
                'instance_id': i.instance_id,
                'ports': i.ports.split(','),
                'host': display_host,
                'server_name': i.docker_config.name if i.docker_config else 'Unknown Server'
            })
        
        # Remove expired containers from database
        for container in containers_to_remove:
            try:
                DockerChallengeTracker.query.filter_by(instance_id=container.instance_id).delete()
                db.session.commit()
            except Exception as e:
                print(f"Error removing expired container from DB: {str(e)}")
        
        return {
            'success': True,
            'data': data
        }


docker_namespace = Namespace("docker", description='Endpoint to retrieve dockerstuff')


@docker_namespace.route("", methods=['POST', 'GET'])
class DockerAPI(Resource):
    """
	This is for creating Docker Challenges. The purpose of this API is to populate the Docker Image Select form
	object in the Challenge Creation Screen. Now returns images grouped by server.
	"""

    @admins_only
    def get(self):
        try:
            servers = DockerConfig.query.filter_by(is_active=True).all()
            if not servers:
                return {
                    'success': False,
                    'data': [{'name': 'Error: No Docker servers configured!'}]
                }, 400
            
            data = []
            
            for server in servers:
                try:
                    # Convert repositories string to list if it exists
                    server_repos = None
                    if server.repositories:
                        server_repos = server.repositories.split(',')
                    
                    images = get_repositories(server, tags=True, repos=server_repos)
                    if images:
                        for image in images:
                            # Format: "ServerName | ImageName" for dropdown display
                            display_name = f"{server.name} | {image}"
                            data.append({
                                'name': display_name,
                                'server_id': server.id,
                                'server_name': server.name,
                                'image_name': image,
                                'server_domain': server.domain
                            })
                except Exception as e:
                    print(f"Error getting images from server {server.name}: {str(e)}")
                    # Add error entry for this server
                    data.append({
                        'name': f"{server.name} | ERROR: {str(e)}",
                        'server_id': server.id,
                        'server_name': server.name,
                        'image_name': None,
                        'error': True
                    })
            
            if not data:
                return {
                    'success': False,
                    'data': [{'name': 'Error: No images found on any server!'}]
                }, 400
            
            return {
                'success': True,
                'data': data
            }
            
        except Exception as e:
            print(f"Error in DockerAPI: {str(e)}")
            return {
                'success': False,
                'data': [{'name': f'Error: {str(e)}'}]
            }, 500



def load(app):
    # Create tables first
    try:
        app.db.create_all()
        print("Database tables created/verified successfully")
    except Exception as e:
        print(f"Error creating database tables: {str(e)}")
    
    # Run database migration
    try:
        migrate_old_config()
    except Exception as e:
        print(f"Migration failed: {str(e)}")
        print("Plugin will continue loading but some features may not work correctly")
    
    CHALLENGE_CLASSES['docker'] = DockerChallengeType
    
    @app.template_filter('datetimeformat')
    def datetimeformat(value, format='%Y-%m-%d %H:%M:%S'):
        return datetime.fromtimestamp(value).strftime(format)
    
    register_plugin_assets_directory(app, base_path='/plugins/docker_challenges/assets')
    
    # Register single Docker menu item - commented out to avoid duplication with dropdown
    # register_admin_plugin_menu_bar(title='Docker', route='/admin/docker_config')
    
    define_docker_admin(app)
    define_docker_status(app)
    CTFd_API_v1.add_namespace(docker_namespace, '/docker')
    CTFd_API_v1.add_namespace(container_namespace, '/container')
    CTFd_API_v1.add_namespace(active_docker_namespace, '/docker_status')
    CTFd_API_v1.add_namespace(kill_container, '/nuke')
    
    # Start the background cleanup thread
    try:
        start_cleanup_thread(app)
        print("Docker challenges plugin loaded with multi-server support and background cleanup")
    except Exception as e:
        print(f"Error starting cleanup thread: {str(e)}")
        print("Plugin loaded but background cleanup is disabled")


def migrate_old_config():
    """
    Migrate old single-server configuration to new multi-server format
    """
    try:
        # First, check if we need to add new columns to existing table
        from sqlalchemy import text
        
        print("Checking database schema for Docker plugin...")
        
        # Check if new columns exist
        columns_to_add = [
            ("name", "VARCHAR(128)"),
            ("domain", "VARCHAR(256)"),
            ("is_active", "BOOLEAN DEFAULT TRUE"),
            ("created_at", "DATETIME"),
            ("last_status_check", "DATETIME"),
            ("status", "VARCHAR(32) DEFAULT 'unknown'"),
            ("status_message", "VARCHAR(512)")
        ]
        
        for column_name, column_def in columns_to_add:
            try:
                # Try to query the column to see if it exists
                result = db.session.execute(text(f"SELECT {column_name} FROM docker_config LIMIT 1")).fetchone()
            except Exception as e:
                if "Unknown column" in str(e) or "no such column" in str(e):
                    print(f"Adding missing column: {column_name}")
                    try:
                        # Add the missing column
                        db.session.execute(text(f"ALTER TABLE docker_config ADD COLUMN {column_name} {column_def}"))
                        db.session.commit()
                        print(f"Successfully added column: {column_name}")
                    except Exception as alter_error:
                        print(f"Error adding column {column_name}: {str(alter_error)}")
                        db.session.rollback()
                else:
                    print(f"Error checking column {column_name}: {str(e)}")
        
        # Now check if we need to add columns to docker_challenge_tracker
        tracker_columns_to_add = [
            ("docker_config_id", "INTEGER")
        ]
        
        for column_name, column_def in tracker_columns_to_add:
            try:
                result = db.session.execute(text(f"SELECT {column_name} FROM docker_challenge_tracker LIMIT 1")).fetchone()
            except Exception as e:
                if "Unknown column" in str(e) or "no such column" in str(e):
                    print(f"Adding missing column to tracker: {column_name}")
                    try:
                        db.session.execute(text(f"ALTER TABLE docker_challenge_tracker ADD COLUMN {column_name} {column_def}"))
                        db.session.commit()
                        print(f"Successfully added tracker column: {column_name}")
                    except Exception as alter_error:
                        print(f"Error adding tracker column {column_name}: {str(alter_error)}")
                        db.session.rollback()
        
        # Check docker_challenge table
        challenge_columns_to_add = [
            ("docker_config_id", "INTEGER")
        ]
        
        for column_name, column_def in challenge_columns_to_add:
            try:
                result = db.session.execute(text(f"SELECT {column_name} FROM docker_challenge LIMIT 1")).fetchone()
            except Exception as e:
                if "Unknown column" in str(e) or "no such column" in str(e):
                    print(f"Adding missing column to challenge: {column_name}")
                    try:
                        db.session.execute(text(f"ALTER TABLE docker_challenge ADD COLUMN {column_name} {column_def}"))
                        db.session.commit()
                        print(f"Successfully added challenge column: {column_name}")
                    except Exception as alter_error:
                        print(f"Error adding challenge column {column_name}: {str(alter_error)}")
                        db.session.rollback()
        
        # Now migrate existing data
        try:
            # Check if we have old config that needs migration
            old_configs = db.session.execute(text("SELECT * FROM docker_config WHERE name IS NULL OR name = ''")).fetchall()
            
            if old_configs:
                print(f"Migrating {len(old_configs)} existing Docker configurations...")
                
                for config_row in old_configs:
                    config_id = config_row[0]  # Assuming id is first column
                    print(f"Migrating config ID: {config_id}")
                    
                    # Update the config with default values
                    db.session.execute(text("""
                        UPDATE docker_config 
                        SET name = 'Main Server',
                            is_active = TRUE,
                            status = 'unknown',
                            created_at = NOW()
                        WHERE id = :config_id AND (name IS NULL OR name = '')
                    """), {"config_id": config_id})
                
                db.session.commit()
                print("Successfully migrated existing configurations")
            
            # Migrate existing challenges
            challenges_without_server = db.session.execute(text(
                "SELECT id FROM docker_challenge WHERE docker_config_id IS NULL"
            )).fetchall()
            
            if challenges_without_server:
                print(f"Migrating {len(challenges_without_server)} existing challenges...")
                
                # Get the first available server
                first_server = db.session.execute(text(
                    "SELECT id FROM docker_config ORDER BY id LIMIT 1"
                )).fetchone()
                
                if first_server:
                    server_id = first_server[0]
                    for challenge_row in challenges_without_server:
                        challenge_id = challenge_row[0]
                        db.session.execute(text("""
                            UPDATE docker_challenge 
                            SET docker_config_id = :server_id 
                            WHERE id = :challenge_id
                        """), {"server_id": server_id, "challenge_id": challenge_id})
                    
                    db.session.commit()
                    print(f"Migrated {len(challenges_without_server)} challenges to server ID: {server_id}")
            
            # Migrate existing container tracker entries
            containers_without_server = db.session.execute(text(
                "SELECT id FROM docker_challenge_tracker WHERE docker_config_id IS NULL"
            )).fetchall()
            
            if containers_without_server:
                print(f"Migrating {len(containers_without_server)} existing container tracker entries...")
                
                first_server = db.session.execute(text(
                    "SELECT id FROM docker_config ORDER BY id LIMIT 1"
                )).fetchone()
                
                if first_server:
                    server_id = first_server[0]
                    for container_row in containers_without_server:
                        container_id = container_row[0]
                        db.session.execute(text("""
                            UPDATE docker_challenge_tracker 
                            SET docker_config_id = :server_id 
                            WHERE id = :container_id
                        """), {"server_id": server_id, "container_id": container_id})
                    
                    db.session.commit()
                    print(f"Migrated {len(containers_without_server)} container tracker entries")
                    
        except Exception as e:
            print(f"Data migration error: {str(e)}")
            db.session.rollback()
            
        print("Database migration completed successfully!")
            
    except Exception as e:
        print(f"Migration error: {str(e)}")
        db.session.rollback()
        # Don't fail the plugin load if migration has issues


# Global cleanup thread variable
cleanup_thread = None

def background_cleanup(app):
    """
    Background thread function that runs every 60 seconds to clean up expired containers
    """
    while True:
        try:
            time.sleep(60)  # Run every 60 seconds
            
            # Use application context for database operations
            with app.app_context():
                # Get all containers from database
                containers = DockerChallengeTracker.query.all()
                current_time = unix_time(datetime.utcnow())
                
                for container in containers:
                    container_age = current_time - container.timestamp
                    
                    # Check if container has expired (older than 5 minutes = 300 seconds)
                    if container_age >= 300:
                        try:
                            # Use the container's associated docker config
                            if container.docker_config:
                                delete_container(container.docker_config, container.instance_id)
                        except Exception as e:
                            print(f"Background cleanup - Error deleting container {container.instance_id}: {str(e)}")
                        
                        try:
                            # Remove from database
                            db.session.delete(container)
                            db.session.commit()
                        except Exception as e:
                            print(f"Background cleanup - Error removing container from database: {str(e)}")
                            
        except Exception as e:
            print(f"Background cleanup - Error in cleanup thread: {str(e)}")
            # Continue running even if there's an error

def start_cleanup_thread(app):
    """
    Start the background cleanup thread if it's not already running
    """
    global cleanup_thread
    
    if cleanup_thread is None or not cleanup_thread.is_alive():
        cleanup_thread = threading.Thread(target=background_cleanup, args=(app,), daemon=True)
        cleanup_thread.start()
        print("Background cleanup thread started")
    else:
        print("Background cleanup thread already running")
        print("Background cleanup thread already running")
