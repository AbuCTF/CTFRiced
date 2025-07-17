import traceback
import threading
import time
from datetime import datetime

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


class DockerConfig(db.Model):
    """
	Docker Config Model. This model stores the config for docker API connections.
	"""
    id = db.Column(db.Integer, primary_key=True)
    hostname = db.Column("hostname", db.String(64), index=True)
    tls_enabled = db.Column("tls_enabled", db.Boolean, default=False, index=True)
    ca_cert = db.Column("ca_cert", db.String(2200), index=True)
    client_cert = db.Column("client_cert", db.String(2000), index=True)
    client_key = db.Column("client_key", db.String(3300), index=True)
    repositories = db.Column("repositories", db.String(1024), index=True)


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

class DockerConfigForm(BaseForm):
    id = HiddenField()
    hostname = StringField(
        "Docker Hostname", description="The Hostname/IP and Port of your Docker Server"
    )
    tls_enabled = RadioField('TLS Enabled?')
    ca_cert = FileField('CA Cert')
    client_cert = FileField('Client Cert')
    client_key = FileField('Client Key')
    repositories = SelectMultipleField('Repositories')
    submit = SubmitField('Submit')


def define_docker_admin(app):
    admin_docker_config = Blueprint('admin_docker_config', __name__, template_folder='templates',
                                    static_folder='assets')

    @admin_docker_config.route("/admin/docker_config", methods=["GET", "POST"])
    @admins_only
    def docker_config():
        docker = DockerConfig.query.filter_by(id=1).first()
        form = DockerConfigForm()
        if request.method == "POST":
            if docker:
                b = docker
            else:
                b = DockerConfig()
            try:
                ca_cert = request.files['ca_cert'].stream.read()
            except Exception:
                ca_cert = ''
            try:
                client_cert = request.files['client_cert'].stream.read()
            except Exception:
                client_cert = ''
            try:
                client_key = request.files['client_key'].stream.read()
            except Exception:
                client_key = ''
            if len(ca_cert) != 0: b.ca_cert = ca_cert
            if len(client_cert) != 0: b.client_cert = client_cert
            if len(client_key) != 0: b.client_key = client_key
            b.hostname = request.form['hostname']
            b.tls_enabled = request.form['tls_enabled']
            if b.tls_enabled == "True":
                b.tls_enabled = True
            else:
                b.tls_enabled = False
            if not b.tls_enabled:
                b.ca_cert = None
                b.client_cert = None
                b.client_key = None
            try:
                b.repositories = ','.join(request.form.to_dict(flat=False)['repositories'])
            except Exception:
                b.repositories = None
            db.session.add(b)
            db.session.commit()
            docker = DockerConfig.query.filter_by(id=1).first()
        try:
            repos = get_repositories(docker)
        except Exception:
            repos = list()
        if len(repos) == 0:
            form.repositories.choices = [("ERROR", "Failed to Connect to Docker")]
        else:
            form.repositories.choices = [(d, d) for d in repos]
        dconfig = DockerConfig.query.first()
        try:
            selected_repos = dconfig.repositories
            if selected_repos == None:
                selected_repos = list()
        # selected_repos = dconfig.repositories.split(',')
        except Exception:
            selected_repos = []
        return render_template("docker_config.html", config=dconfig, form=form, repos=selected_repos)

    app.register_blueprint(admin_docker_config)


def define_docker_status(app):
    admin_docker_status = Blueprint('admin_docker_status', __name__, template_folder='templates',
                                    static_folder='assets')

    @admin_docker_status.route("/admin/docker_status", methods=["GET", "POST"])
    @admins_only
    def docker_admin():
        docker_config = DockerConfig.query.filter_by(id=1).first()
        docker_tracker = DockerChallengeTracker.query.all()
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
        return render_template("admin_docker_status.html", dockers=docker_tracker)

    app.register_blueprint(admin_docker_status)


kill_container = Namespace("nuke", description='Endpoint to nuke containers')


@kill_container.route("", methods=['POST', 'GET'])
class KillContainerAPI(Resource):
    @admins_only
    def get(self):
        try:
            container = request.args.get('container')
            full = request.args.get('all')
            docker_config = DockerConfig.query.filter_by(id=1).first()
            
            if not docker_config:
                return {"success": False, "message": "Docker configuration not found"}, 500
                
            docker_tracker = DockerChallengeTracker.query.all()
            
            if full == "true":
                for c in docker_tracker:
                    try:
                        delete_container(docker_config, c.instance_id)
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
                    delete_container(docker_config, container)
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
                        if repos:
                            if not i['RepoTags'][0].split(':')[0] in repos:
                                continue
                        if not tags:
                            result.append(i['RepoTags'][0].split(':')[0])
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
        
        return result, data
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

		:param request:
		:return:
		"""
        data = request.form or request.get_json()
        challenge = DockerChallenge(**data)
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
        docker = DockerConfig.query.filter_by(id=1).first()
        try:
            if is_teams_mode():
                docker_containers = DockerChallengeTracker.query.filter_by(
                    docker_image=challenge.docker_image).filter_by(team_id=team.id).first()
            else:
                docker_containers = DockerChallengeTracker.query.filter_by(
                    docker_image=challenge.docker_image).filter_by(user_id=user.id).first()
            delete_container(docker, docker_containers.instance_id)
            DockerChallengeTracker.query.filter_by(instance_id=docker_containers.instance_id).delete()
        except:
            pass
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
            
            docker = DockerConfig.query.filter_by(id=1).first()
            if not docker:
                return abort(500, "Docker configuration not found")
            
            # Check if container exists in repository
            try:
                repositories = get_repositories(docker, tags=True)
                if container not in repositories:
                    return abort(403, f"Container {container} not present in the repository.")
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
                                delete_container(docker, i.instance_id)
                                DockerChallengeTracker.query.filter_by(instance_id=i.instance_id).delete()
                                db.session.commit()
                            except Exception as e:
                                print(f"Error removing old team container: {str(e)}")
                    else:
                        if i.user_id is not None and int(session.id) == int(i.user_id) and container_age >= 7200:
                            try:
                                delete_container(docker, i.instance_id)
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
                    delete_container(docker, check.instance_id)
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
                    delete_container(docker, check.instance_id)
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
                        delete_container(docker, i.instance_id)
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
                entry = DockerChallengeTracker(
                    team_id=session.id if is_teams_mode() else None,
                    user_id=session.id if not is_teams_mode() else None,
                    docker_image=container,
                    timestamp=unix_time(datetime.utcnow()),
                    revert_time=unix_time(datetime.utcnow()) + 300,
                    instance_id=create[0]['Id'],
                    ports=','.join([p[0]['HostPort'] for p in ports]),
                    host=str(docker.hostname).split(':')[0],
                    challenge=challenge
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
        docker = DockerConfig.query.filter_by(id=1).first()
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
                    delete_container(docker, container.instance_id)
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
                    delete_container(docker, i.instance_id)
                    containers_to_remove.append(i)
                except Exception as e:
                    print(f"Error deleting expired container {i.instance_id}: {str(e)}")
                    # Only remove from DB if Docker deletion was successful
                    continue
                continue
                
            data.append({
                'id': i.id,
                'team_id': i.team_id,
                'user_id': i.user_id,
                'docker_image': i.docker_image,
                'timestamp': i.timestamp,
                'revert_time': i.revert_time,
                'instance_id': i.instance_id,
                'ports': i.ports.split(','),
                'host': str(docker.hostname).split(':')[0]
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
	object in the Challenge Creation Screen.
	"""

    @admins_only
    def get(self):
        docker = DockerConfig.query.filter_by(id=1).first()
        images = get_repositories(docker, tags=True, repos=docker.repositories)
        if images:
            data = list()
            for i in images:
                data.append({'name': i})
            return {
                'success': True,
                'data': data
            }
        else:
            return {
                       'success': False,
                       'data': [
                           {
                               'name': 'Error in Docker Config!'
                           }
                       ]
                   }, 400



def load(app):
    app.db.create_all()
    CHALLENGE_CLASSES['docker'] = DockerChallengeType
    @app.template_filter('datetimeformat')
    def datetimeformat(value, format='%Y-%m-%d %H:%M:%S'):
        return datetime.fromtimestamp(value).strftime(format)
    register_plugin_assets_directory(app, base_path='/plugins/docker_challenges/assets')
    define_docker_admin(app)
    define_docker_status(app)
    CTFd_API_v1.add_namespace(docker_namespace, '/docker')
    CTFd_API_v1.add_namespace(container_namespace, '/container')
    CTFd_API_v1.add_namespace(active_docker_namespace, '/docker_status')
    CTFd_API_v1.add_namespace(kill_container, '/nuke')
    
    # Start the background cleanup thread
    start_cleanup_thread()
    print("Docker challenges plugin loaded with background cleanup")


# Global cleanup thread variable
cleanup_thread = None

def background_cleanup():
    """
    Background thread function that runs every 60 seconds to clean up expired containers
    """
    while True:
        try:
            time.sleep(60)  # Run every 60 seconds
            
            # Get all containers from database
            containers = DockerChallengeTracker.query.all()
            current_time = unix_time(datetime.utcnow())
            
            for container in containers:
                container_age = current_time - container.timestamp
                
                # Check if container has expired (older than 5 minutes = 300 seconds)
                if container_age >= 300:
                    try:
                        # Get docker configuration
                        docker = DockerConfig.query.first()
                        if docker:
                            # Delete the container
                            delete_container(docker, container.instance_id)
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

def start_cleanup_thread():
    """
    Start the background cleanup thread if it's not already running
    """
    global cleanup_thread
    
    if cleanup_thread is None or not cleanup_thread.is_alive():
        cleanup_thread = threading.Thread(target=background_cleanup, daemon=True)
        cleanup_thread.start()
        print("Background cleanup thread started")
    else:
        print("Background cleanup thread already running")
