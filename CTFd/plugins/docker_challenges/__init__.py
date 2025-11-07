import traceback
import logging
import threading
import time
import os
import re
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

from CTFd.plugins.challenges import BaseChallenge, CHALLENGE_CLASSES, get_chal_class, ChallengeResponse
from CTFd.plugins.migrations import upgrade
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
from flask import request, Blueprint, jsonify, abort, render_template, url_for, redirect, session, current_app
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
	Now supports multi-image challenges with stack tracking.
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
    
    # New fields for multi-image support
    stack_id = db.Column("stack_id", db.String(128), nullable=True, index=True)  # Group containers in same stack
    service_name = db.Column("service_name", db.String(64), nullable=True)  # Service name within compose stack
    is_primary = db.Column("is_primary", db.Boolean, default=False)  # Primary service flag for port display
    network_name = db.Column("network_name", db.String(128), nullable=True)  # Docker network name
    
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
                
                try:
                    db.session.add(server)
                    db.session.commit()
                except Exception as e:
                    db.session.rollback()
                    current_app.logger.error(f"Error saving server to database: {str(e)}")
                    form.errors['general'] = [f"Error saving server: {str(e)}"]
                    raise
                
                # Test the server connection
                update_server_status(server)
                
                return redirect(url_for('admin_docker_config.docker_config_list'))
                
            except Exception as e:
                current_app.logger.error(f"Error adding server: {str(e)}")
                form.errors['general'] = [f"Error adding server: {str(e)}"]
        
        # Get repositories for form (try to get from any available server)
        try:
            repos = []
            servers = DockerConfig.query.filter_by(is_active=True).all()
            for server in servers:
                try:
                    server_repos = get_repositories(server, group_compose=True)
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
                
                try:
                    db.session.commit()
                except Exception as e:
                    db.session.rollback()
                    current_app.logger.error(f"Error updating server in database: {str(e)}")
                    form.errors['general'] = [f"Error updating server: {str(e)}"]
                    raise
                
                # Test the server connection
                update_server_status(server)
                
                return redirect(url_for('admin_docker_config.docker_config_list'))
                
            except Exception as e:
                current_app.logger.error(f"Error updating server: {str(e)}")
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
            repos = get_repositories(server, group_compose=True)
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
            
            try:
                db.session.delete(server)
                db.session.commit()
            except Exception as e:
                db.session.rollback()
                current_app.logger.error(f"Error deleting server from database: {str(e)}")
                raise
            
            return jsonify({"success": True, "message": "Server deleted successfully"}), 200
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
            }), 200
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
        all_containers = DockerChallengeTracker.query.all()
        
        # Group containers by stack_id (for multi-image) and single containers
        stacks = {}
        single_containers = []
        
        for container in all_containers:
            if container.stack_id:
                # Multi-image stack - group by stack_id
                if container.stack_id not in stacks:
                    stacks[container.stack_id] = {
                        'containers': [],
                        'primary': None,
                        'stack_id': container.stack_id
                    }
                stacks[container.stack_id]['containers'].append(container)
                if container.is_primary:
                    stacks[container.stack_id]['primary'] = container
            else:
                # Single container
                single_containers.append(container)
        
        # Create display list with one entry per stack/container
        docker_tracker = []
        
        # Add stack entries (show primary container info, but list all images)
        for stack_id, stack_info in stacks.items():
            primary_container = stack_info['primary'] or stack_info['containers'][0]
            
            # Create a combined entry representing the whole stack
            # Use the primary container as base, but create a simple object to avoid DB issues
            stack_entry = type('StackEntry', (), {})()
            stack_entry.id = primary_container.id
            stack_entry.team_id = primary_container.team_id
            stack_entry.user_id = primary_container.user_id
            stack_entry.timestamp = primary_container.timestamp
            stack_entry.revert_time = primary_container.revert_time
            stack_entry.instance_id = primary_container.instance_id
            stack_entry.ports = primary_container.ports
            stack_entry.host = primary_container.host
            stack_entry.challenge = primary_container.challenge  # Keep the actual challenge name
            stack_entry.docker_config_id = primary_container.docker_config_id
            stack_entry.docker_config = primary_container.docker_config
            stack_entry.stack_id = primary_container.stack_id
            stack_entry.service_name = primary_container.service_name
            stack_entry.network_name = primary_container.network_name
            
            # Customize display fields for multi-image stacks
            stack_entry.docker_image = f"{len(stack_info['containers'])} images: " + ", ".join([c.docker_image for c in stack_info['containers']])
            stack_entry.is_stack = True
            stack_entry.container_count = len(stack_info['containers'])
            
            docker_tracker.append(stack_entry)
        
        # Add single container entries
        for container in single_containers:
            container.is_stack = False
            container.container_count = 1
            docker_tracker.append(container)
        
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
                # Delete all containers
                stacks_to_delete = set()  # Track unique stack IDs
                singles_to_delete = []    # Track single containers
                
                for c in docker_tracker:
                    if c.stack_id:
                        stacks_to_delete.add(c.stack_id)
                    else:
                        singles_to_delete.append(c)
                
                # Delete all compose stacks
                for stack_id in stacks_to_delete:
                    try:
                        stack_containers = DockerChallengeTracker.query.filter_by(stack_id=stack_id).first()
                        if stack_containers and stack_containers.docker_config:
                            delete_compose_stack(stack_containers.docker_config, stack_id)
                    except Exception as e:
                        current_app.logger.error(f"Error deleting stack {stack_id}: {str(e)}")
                        continue
                
                # Delete single containers
                for c in singles_to_delete:
                    try:
                        if c.docker_config:
                            delete_container(c.docker_config, c.instance_id)
                        tracker_to_delete = DockerChallengeTracker.query.filter_by(instance_id=c.instance_id).first()
                        if tracker_to_delete:
                            db.session.delete(tracker_to_delete)
                        db.session.commit()
                    except Exception as e:
                        current_app.logger.error(f"Error deleting container {c.instance_id}: {str(e)}")
                        continue

            elif container != 'null' and container in [c.instance_id for c in docker_tracker]:
                try:
                    container_to_delete = DockerChallengeTracker.query.filter_by(instance_id=container).first()
                    if not container_to_delete:
                        return {"success": False, "message": "Container not found in tracker"}, 404
                    
                    # Check if this container is part of a compose stack
                    if container_to_delete.stack_id:
                        # This is part of a multi-image stack - delete the entire stack
                        current_app.logger.info(f"Deleting compose stack {container_to_delete.stack_id} (triggered by container {container})")
                        if container_to_delete.docker_config:
                            success = delete_compose_stack(container_to_delete.docker_config, container_to_delete.stack_id)
                            if not success:
                                return {"success": False, "message": "Failed to delete compose stack"}, 500
                        else:
                            return {"success": False, "message": "No Docker config found for stack"}, 500
                    else:
                        # Single container - delete normally
                        current_app.logger.info(f"Deleting single container {container}")
                        if container_to_delete.docker_config:
                            delete_container(container_to_delete.docker_config, container)
                        tracker_to_delete = DockerChallengeTracker.query.filter_by(instance_id=container).first()
                        if tracker_to_delete:
                            db.session.delete(tracker_to_delete)
                        db.session.commit()
                        
                except Exception as e:
                    current_app.logger.error(f"Error deleting container {container}: {str(e)}")
                    return {"success": False, "message": f"Error deleting container: {str(e)}"}, 500

            else:
                return {"success": False, "message": "Invalid container specified"}, 400
                
            return {"success": True, "message": "Container(s) deleted successfully"}, 200
            
        except Exception as e:
            current_app.logger.error(f"Error in nuke endpoint: {str(e)}")
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
        current_app.logger.error(f"Error updating server status: {str(e)}")
        return False, f"Update failed: {str(e)}"


def get_best_server_for_image(image_name):
    """
    Find the best available server that has the requested image
    Returns the DockerConfig object or None
    """
    try:
        # Get all active servers
        servers = DockerConfig.query.filter_by(is_active=True).all()
        
        if not servers:
            current_app.logger.warning(f"No active Docker servers configured")
            return None
        
        # First pass: try to find a server that has the image
        for server in servers:
            try:
                # Check if server is healthy first
                is_healthy, health_msg = check_server_health(server)
                if not is_healthy:
                    current_app.logger.warning(f"Server {server.name} is unhealthy: {health_msg}")
                    continue
                
                # Try to check if server has the image
                try:
                    repositories = get_repositories(server, tags=True)
                    if image_name in repositories:
                        current_app.logger.debug(f"Found image {image_name} on server {server.name}")
                        return server
                    else:
                        current_app.logger.debug(f"Image {image_name} not found on server {server.name}")
                except Exception as repo_e:
                    current_app.logger.warning(f"Could not get repository list from {server.name}: {str(repo_e)}")
                    # Don't skip this server - it might still be usable for pulling
                    
            except Exception as e:
                current_app.logger.error(f"Error checking server {server.name}: {str(e)}")
                continue
        
        # Second pass: if no server has the image, return the first healthy server
        # This allows Docker to auto-pull the image
        for server in servers:
            try:
                is_healthy, health_msg = check_server_health(server)
                if is_healthy:
                    current_app.logger.info(f"No server has image {image_name}, returning healthy server {server.name} for auto-pull")
                    return server
            except Exception as e:
                current_app.logger.error(f"Error checking server health for {server.name}: {str(e)}")
                continue
        
        current_app.logger.error(f"No healthy servers found for image {image_name}")
        return None
    except Exception as e:
        current_app.logger.error(f"Error finding best server: {str(e)}")
        return None


def do_request(docker, url, headers=None, method='GET', timeout=30, data=None):
    tls = docker.tls_enabled
    prefix = 'https' if tls else 'http'
    host = docker.hostname
    URL_TEMPLATE = '%s://%s' % (prefix, host)
    
    current_app.logger.info(f"do_request: {method} {URL_TEMPLATE}{url} (TLS: {tls})")
    
    try:
        if tls:
            cert, verify = get_client_cert(docker)
            
            # Check if certificates were created successfully
            if cert is None or verify is None:
                current_app.logger.error(f"Failed to create TLS certificates for {docker.name}. Cannot connect to Docker API.")
                return None
            
            # Make TLS request with proper error handling
            try:
                if method == 'GET':
                    r = requests.get(url=f"%s{url}" % URL_TEMPLATE, cert=cert, verify=verify, headers=headers, timeout=timeout)
                elif method == 'DELETE':
                    r = requests.delete(url=f"%s{url}" % URL_TEMPLATE, cert=cert, verify=verify, headers=headers, timeout=timeout)
                elif method == 'POST':
                    r = requests.post(url=f"%s{url}" % URL_TEMPLATE, cert=cert, verify=verify, headers=headers, timeout=timeout, data=data)
                
                # Clean up the cert files after successful/failed request
                cleanup_files = []
                if cert:
                    cleanup_files.extend(cert)
                if verify:
                    cleanup_files.append(verify)
                
                for file_path in cleanup_files:
                    if file_path and os.path.exists(file_path):
                        try:
                            os.unlink(file_path)
                        except Exception as cleanup_error:
                            current_app.logger.warning(f"Failed to cleanup cert file {file_path}: {cleanup_error}")
                
                return r
                
            except requests.exceptions.SSLError as ssl_error:
                current_app.logger.error(f"SSL/TLS error connecting to {URL_TEMPLATE}{url}: {str(ssl_error)}")
                current_app.logger.error(f"This usually means: 1) Client certificates are invalid, 2) Server requires different certificates, 3) Certificate chain is incomplete")
                return None
        else:
            # Non-TLS request
            if method == 'GET':
                r = requests.get(url=f"%s{url}" % URL_TEMPLATE, headers=headers, timeout=timeout)
            elif method == 'DELETE':
                r = requests.delete(url=f"%s{url}" % URL_TEMPLATE, headers=headers, timeout=timeout)
            elif method == 'POST':
                r = requests.post(url=f"%s{url}" % URL_TEMPLATE, headers=headers, timeout=timeout, data=data)
            return r
            
    except requests.exceptions.Timeout:
        current_app.logger.error(f"Timeout connecting to {URL_TEMPLATE}{url} (waited {timeout}s)")
        return None
    except requests.exceptions.ConnectionError as conn_error:
        current_app.logger.error(f"Connection error to {URL_TEMPLATE}{url}: {str(conn_error)}")
        current_app.logger.error(f"Check: 1) Server is running, 2) Port {host.split(':')[-1]} is open, 3) Firewall allows connection")
        return None
    except Exception as e:
        current_app.logger.error(f"Unexpected error connecting to {URL_TEMPLATE}{url}: {str(e)}")
        return None


def get_client_cert(docker):
    # Create TLS certificates for Docker API authentication
    try:
        ca = docker.ca_cert
        client = docker.client_cert
        ckey = docker.client_key
        
        # Validate that all certificate fields are present and not empty
        if not ca or not client or not ckey:
            current_app.logger.error(f"Missing TLS certificates for server {docker.name}. CA: {bool(ca)}, Client: {bool(client)}, Key: {bool(ckey)}")
            return None, None
        
        # Check if certificates are properly formatted (basic validation)
        if not (ca.strip().startswith('-----BEGIN') and client.strip().startswith('-----BEGIN') and ckey.strip().startswith('-----BEGIN')):
            current_app.logger.error(f"Invalid certificate format for server {docker.name}")
            return None, None
        
        # Create temporary files with proper cleanup
        ca_file = tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.pem')
        ca_file.write(ca.strip())
        ca_file.close()
        
        client_file = tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.pem')
        client_file.write(client.strip())
        client_file.close()
        
        key_file = tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.pem')
        key_file.write(ckey.strip())
        key_file.close()
        
        CERT = (client_file.name, key_file.name)
        current_app.logger.info(f"Created TLS certificates for server {docker.name}")
        return CERT, ca_file.name
    except Exception as e:
        current_app.logger.error(f"Error creating TLS certificates for server {docker.name}: {str(e)}")
        return None, None


def parse_environment_vars(env_vars_text):
    """
    Parse and validate environment variables from text format
    
    Args:
        env_vars_text: String containing KEY=VALUE pairs, one per line
        
    Returns:
        tuple: (list of valid env vars, list of validation errors)
    """
    env_vars = []
    errors = []
    
    if not env_vars_text:
        return env_vars, errors
    
    lines = env_vars_text.strip().split('\n')
    for line_num, line in enumerate(lines, 1):
        line = line.strip()
        
        # Skip empty lines and comments
        if not line or line.startswith('#'):
            continue
        
        # Validate format: KEY=VALUE
        if '=' not in line:
            errors.append(f"Line {line_num}: Missing '=' separator. Format should be KEY=VALUE")
            continue
        
        key, _, value = line.partition('=')
        key = key.strip()
        value = value.strip()
        
        # Validate key format (alphanumeric and underscore only)
        import re
        if not re.match(r'^[A-Z][A-Z0-9_]*$', key):
            errors.append(f"Line {line_num}: Invalid key '{key}'. Keys should be UPPERCASE and contain only letters, numbers, and underscores")
            continue
        
        # Warn about potentially sensitive values (but still allow them)
        if not value:
            errors.append(f"Line {line_num}: Empty value for key '{key}'")
            continue
        
        # Add the validated environment variable
        env_vars.append(f"{key}={value}")
    
    return env_vars, errors


# For the Docker Config Page. Gets the Current Repositories available on the Docker Server.
def get_repositories(docker, tags=False, repos=False, group_compose=False, challenge_id=None):
    """
    Get images from Docker server, optionally group compose-related images
    
    Args:
        docker: DockerConfig instance
        tags: Include tags in image names
        repos: Filter by allowed repositories list
        group_compose: Group compose-related images together
        challenge_id: Optional challenge ID to filter images by challenge context
    
    Returns:
        List of images or grouped compose projects
    """
    try:
        current_app.logger.info(f"Fetching repositories from Docker server: {docker.name} ({docker.hostname})")
        current_app.logger.info(f"get_repositories: Making Docker API request to {docker.hostname}")
        r = do_request(docker, '/images/json?all=1')
        if r is None:
            current_app.logger.error(f"Docker API request returned None for /images/json on {docker.hostname}")
            return []
        
        if not hasattr(r, 'status_code') or r.status_code != 200:
            current_app.logger.error(f"Docker API returned status {r.status_code if hasattr(r, 'status_code') else 'unknown'} on {docker.hostname}")
            return []
        
        result = list()
        compose_groups = {}  # Group related images by project name and challenge
        
        try:
            images = r.json()
            
            # First pass: collect all images with their metadata
            image_metadata = {}
            for i in images:
                if not i['RepoTags'] or i['RepoTags'] == [] or i['RepoTags'][0].split(':')[0] == '<none>':
                    continue
                
                image_name = i['RepoTags'][0].split(':')[0] if not tags else i['RepoTags'][0]
                
                # Filter by allowed repositories if specified
                if repos:
                    base_name = image_name.split(':')[0]
                    if base_name not in repos:
                        continue
                
                # Get image labels for challenge identification
                image_labels = i.get('Labels', {}) or {}
                image_challenge_id = image_labels.get('ctf.challenge_id')
                image_stack_id = image_labels.get('ctf.stack_id')
                
                image_metadata[image_name] = {
                    'labels': image_labels,
                    'challenge_id': image_challenge_id,
                    'stack_id': image_stack_id,
                    'raw_data': i
                }
                

            
            # Second pass: group images intelligently
            for image_name, metadata in image_metadata.items():
                image_challenge_id = metadata['challenge_id']
                
                # If we're filtering by challenge and this image doesn't match, skip it
                if challenge_id and image_challenge_id and str(image_challenge_id) != str(challenge_id):

                    continue
                
                # Group compose images if requested (handle both underscores and hyphens)
                if group_compose and ('_' in image_name or '-' in image_name):
                    # Extract compose project name (everything before last separator)
                    base_name = image_name.split(':')[0] if tags else image_name
                    
                    # Try underscore first, then hyphen
                    if '_' in base_name:
                        parts = base_name.split('_')
                        separator = '_'
                    elif '-' in base_name:
                        parts = base_name.split('-')
                        separator = '-'
                    else:
                        parts = [base_name]
                        separator = ''
                    
                    if len(parts) >= 2:
                        last_part = parts[-1]
                        
                        # Check if the last part is a version number (e.g., "1.0", "2.0", "v1", "v2")
                        # If so, don't treat this as a compose stack
                        is_version = bool(re.match(r'^(v?\d+(\.\d+)*|latest|stable|dev)$', last_part, re.IGNORECASE))
                        
                        if is_version:
                            # This is a versioned image, not a compose stack
                            result.append(image_name)  # Treat as single image
                        else:
                            # This looks like a compose stack (e.g., "project-frontend", "project-backend")
                            project_name = separator.join(parts[:-1])  # e.g., "h7tex" from "h7tex-backend"
                            service_name = last_part                   # e.g., "backend" from "h7tex-backend"
                            
                            # Create challenge-aware group key to prevent collisions
                            if image_challenge_id:
                                # Use challenge ID in group key for labeled images
                                group_key = f"{project_name}_chal_{image_challenge_id}"
                                display_name = project_name
                            else:
                                # For unlabeled images, use project name but mark as unlabeled
                                group_key = f"{project_name}_unlabeled"
                                display_name = f"{project_name} (unlabeled)"
                            

                            
                            if group_key not in compose_groups:
                                compose_groups[group_key] = {
                                    'images': [],
                                    'services': [],
                                    'project_name': project_name,
                                    'display_name': display_name,
                                    'challenge_id': image_challenge_id,
                                    'is_labeled': bool(image_challenge_id)
                                }
                            
                            compose_groups[group_key]['images'].append(image_name)
                            compose_groups[group_key]['services'].append(service_name)
                    else:
                        result.append(image_name)  # Single image
                else:
                    result.append(image_name)  # Single image
                    
        except Exception as e:
            current_app.logger.error(f"Failed to parse Docker images response: {str(e)}")
            return []
        
        # Add compose groups as multi-image options
        final_result = []
        if group_compose:
            # Add individual images that aren't part of compose groups
            for item in result:
                if isinstance(item, str):  # Regular image
                    final_result.append(item)
            
            # Apply cross-challenge contamination prevention
            compose_groups = prevent_cross_challenge_contamination(compose_groups, challenge_id)
            
            # Add compose groups (with collision prevention)
            for group_key, data in compose_groups.items():
                if len(data['images']) > 1:  # Only groups with multiple images
                    project_name = data['project_name']
                    challenge_info = f" (Challenge {data['challenge_id']})" if data['challenge_id'] else ""
                    label_info = "" if data['is_labeled'] else " [Unlabeled]"
                    

                    
                    final_result.append({
                        'type': 'compose_group',
                        'name': project_name,
                        'group_key': group_key,  # Internal key for uniqueness
                        'images': data['images'],
                        'services': data['services'],
                        'display_name': f"{project_name} ({len(data['images'])} images){label_info}{challenge_info}",
                        'image_count': len(data['images']),
                        'challenge_id': data['challenge_id'],
                        'is_labeled': data['is_labeled']
                    })
                else:
                    # Single image in project - add to regular results as individual image
                    if data['images']:
                        final_result.extend(data['images'])

            return final_result
        
        return list(set(result))
        
    except Exception as e:
        current_app.logger.error(f"Error in get_repositories(): {str(e)}")
        return []
        import traceback
        traceback.print_exc()
        return []


def get_unavailable_ports(docker):
    try:
        r = do_request(docker, '/containers/json?all=1')
        if r is None:
            current_app.logger.error("Docker API request returned None for /containers/json")
            return []
        
        if not hasattr(r, 'status_code') or r.status_code != 200:
            current_app.logger.error(f"Docker API returned status {r.status_code if hasattr(r, 'status_code') else 'unknown'}")
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
            current_app.logger.error(f"Failed to parse Docker containers response: {str(e)}")
            return []
        
        return result
    except Exception as e:
        current_app.logger.error(f"Error in get_unavailable_ports(): {str(e)}")
        import traceback
        traceback.print_exc()
        return []


def get_required_ports(docker, image):
    try:

        
        # Validate image name format
        if not image or '|' in image:
            raise Exception(f"Invalid image name format: '{image}' - may contain display formatting")
        
        r = do_request(docker, f'/images/{image}/json?all=1')
        if r is None:
            current_app.logger.error(f"Docker API request returned None for /images/{image}/json")
            return []
        
        if not hasattr(r, 'status_code') or r.status_code != 200:
            error_msg = f"Docker API returned status {r.status_code if hasattr(r, 'status_code') else 'unknown'}"
            if hasattr(r, 'text'):
                error_msg += f": {r.text}"
            current_app.logger.error(error_msg)
            return []
        
        try:
            image_info = r.json()
            if 'Config' in image_info and 'ExposedPorts' in image_info['Config'] and image_info['Config']['ExposedPorts']:
                result = list(image_info['Config']['ExposedPorts'].keys())

                return result
            else:
                current_app.logger.warning(f"No exposed ports found for image {image}")
                return []
        except Exception as e:
            current_app.logger.error(f"Failed to parse image info response: {str(e)}")
            return []
    except Exception as e:
        current_app.logger.error(f"Error in get_required_ports() for image '{image}': {str(e)}")
        import traceback
        traceback.print_exc()
        return []


def create_container(docker, image, team, portbl, challenge_id=None):
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
            current_app.logger.error(f"Failed to get required ports: {str(e)}")
            raise Exception(f"Failed to get required ports for image {image}")
        
        team = hashlib.md5(team.encode("utf-8")).hexdigest()[:10]
        
        # Sanitize image name for Docker container naming compliance
        # Docker container names must be lowercase alphanumeric with some special chars
        import re
        image_safe = re.sub(r'[^a-zA-Z0-9_.-]', '_', image.lower())
        # Remove any leading/trailing underscores or dots
        image_safe = image_safe.strip('_.-')
        # Ensure it doesn't start with a dot or dash
        if image_safe.startswith('.') or image_safe.startswith('-'):
            image_safe = 'img_' + image_safe[1:]
        # Ensure it's not empty
        if not image_safe:
            image_safe = 'container'
            
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
                                    current_app.logger.warning(f"Error removing from database: {str(db_e)}")
                                
                            except Exception as rm_e:
                                current_app.logger.warning(f"Error removing existing container: {str(rm_e)}")
                                # Continue anyway, the create might still work
                            break
        except Exception as e:
            current_app.logger.warning(f"Error checking for existing containers: {str(e)}")
            # Continue anyway
        
        assigned_ports = dict()
        for i in needed_ports:
            # Use the same port allocation method as compose stacks
            assigned_port = get_available_port(docker)
            assigned_ports['%s/tcp' % assigned_port] = {}
        
        ports = dict()
        bindings = dict()
        tmp_ports = list(assigned_ports.keys())
        for i in needed_ports:
            ports[i] = {}
            host_port_key = tmp_ports.pop()
            # Extract just the port number from the format "30000/tcp"
            host_port = host_port_key.split('/')[0]
            bindings[i] = [{"HostPort": host_port}]
        
        # Prepare container labels for challenge identification
        labels = {
            "ctf.managed": "true",
            "ctf.team": team,
            "ctf.image": image,
            "ctf.created_at": str(int(time.time()))
        }
        
        # Add challenge-specific labels if provided
        if challenge_id:
            labels["ctf.challenge_id"] = str(challenge_id)
            labels["ctf.type"] = "single"
        
        # Get environment variables from challenge if they exist
        env_vars = []
        if challenge_id:
            try:
                challenge = DockerChallenge.query.filter_by(id=challenge_id).first()
                if challenge and challenge.environment_vars:
                    # Parse and validate environment variables
                    env_vars, errors = parse_environment_vars(challenge.environment_vars)
                    if errors:
                        current_app.logger.warning(f"Environment variable validation errors for challenge {challenge_id}: {', '.join(errors)}")
                    if env_vars:
                        current_app.logger.info(f"Loaded {len(env_vars)} environment variables for challenge {challenge_id}")
            except Exception as e:
                current_app.logger.warning(f"Error parsing environment variables for challenge {challenge_id}: {str(e)}")
        
        headers = {'Content-Type': "application/json"}
        container_config = {
            "Image": image, 
            "ExposedPorts": ports, 
            "HostConfig": {"PortBindings": bindings},
            "Labels": labels
        }
        
        # Add environment variables if any
        if env_vars:
            container_config["Env"] = env_vars

        data = json.dumps(container_config)
        
        if tls:
            cert, verify = get_client_cert(docker)
            r = requests.post(url="%s/containers/create?name=%s" % (URL_TEMPLATE, container_name), cert=cert,
                          verify=verify, data=data, headers=headers)
            if r.status_code not in [200, 201]:
                current_app.logger.error(f"Container creation failed with status {r.status_code}: {r.text}")
                raise Exception(f"Container creation failed: {r.text}")
                
            result = r.json()
            
            s = requests.post(url="%s/containers/%s/start" % (URL_TEMPLATE, result['Id']), cert=cert, verify=verify,
                              headers=headers)
            if s.status_code not in [200, 204]:
                current_app.logger.error(f"Container start failed with status {s.status_code}: {s.text}")
                raise Exception(f"Container start failed: {s.text}")
                
            # Clean up the cert files:
            for file_path in [*cert, verify]:
                if file_path:
                    Path(file_path).unlink(missing_ok=True)
        else:
            r = requests.post(url="%s/containers/create?name=%s" % (URL_TEMPLATE, container_name),
                              data=data, headers=headers)
            
            if r.status_code not in [200, 201]:
                current_app.logger.error(f"Container creation failed with status {r.status_code}: {r.text}")
                raise Exception(f"Container creation failed: {r.text}")
                
            result = r.json()
            
            # name conflicts are not handled properly
            s = requests.post(url="%s/containers/%s/start" % (URL_TEMPLATE, result['Id']), headers=headers)
            if s.status_code not in [200, 204]:
                current_app.logger.error(f"Container start failed with status {s.status_code}: {s.text}")
                raise Exception(f"Container start failed: {s.text}")
        
        return result, data, docker  # Return the docker config used
    except Exception as e:
        current_app.logger.error(f"Error in create_container(): {str(e)}")
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
            current_app.logger.warning(f"Failed to connect to Docker API for container {instance_id}")
            return False
            
        if hasattr(response, 'status_code') and response.status_code not in [200, 204, 404]:
            current_app.logger.warning(f"Container deletion returned status code {response.status_code}")
            return False
            
        return True
    except Exception as e:
        current_app.logger.error(f"Error deleting container {instance_id}: {str(e)}")
        return False


def get_available_port(docker, start_port=30000, end_port=60000):
    """
    Get an available port on the Docker host
    """
    try:
        # Get list of used ports
        used_ports = get_unavailable_ports(docker)

        
        # Convert used_ports to integers for consistent comparison
        used_ports_int = set()
        for port in used_ports:
            try:
                used_ports_int.add(int(port))
            except (ValueError, TypeError):
                pass
        
        # Find available port in range
        import random
        available_ports = []
        for port in range(start_port, end_port):
            if port not in used_ports_int:
                available_ports.append(port)
        
        if available_ports:
            # Return a random port from available ones instead of always the first
            selected_port = random.choice(available_ports)

            return selected_port
        
        # Fallback to random port if no port found in range
        fallback_port = random.choice(range(start_port, end_port))

        return fallback_port
    except Exception as e:
        current_app.logger.error(f"Error getting available port: {str(e)}")
        import random
        fallback_port = random.choice(range(30000, 60000))

        return fallback_port


def prevent_cross_challenge_contamination(compose_groups, challenge_id):
    """
    Prevent images from different challenges being grouped together
    by applying stricter filtering for unlabeled images
    
    Args:
        compose_groups: Dictionary of compose groups
        challenge_id: Current challenge ID (if any)
    
    Returns:
        Filtered compose groups dictionary
    """
    if not challenge_id:
        return compose_groups
    
    filtered_groups = {}
    
    for group_key, data in compose_groups.items():
        group_challenge_id = data.get('challenge_id')
        
        # If image has a challenge label and it doesn't match current challenge, skip it
        if group_challenge_id and str(group_challenge_id) != str(challenge_id):

            continue
        
        # For unlabeled images, use additional heuristics
        if not group_challenge_id:
            # Apply stricter naming rules to prevent contamination
            project_name = data.get('project_name', '')
            
            # If project name contains 'chal', 'challenge', or numbers that might indicate different challenges
            # be more conservative about grouping
            if any(keyword in project_name.lower() for keyword in ['chal', 'challenge', 'ctf']):
                # Only include if it's a very clear match (you could add more logic here)
                pass  # Placeholder for future logic
            
            # Add warning to display name for unlabeled images
            if 'display_name' in data:
                data['display_name'] = data['display_name'].replace('(unlabeled)', '(unlabeled - use caution)')
        
        filtered_groups[group_key] = data
    
    return filtered_groups


def parse_image_name_from_display(display_name):
    """
    Parse the actual Docker image name from the display format used in the frontend
    
    Args:
        display_name: The display name format like "Server Name | image-name" or just "image-name"
        
    Returns:
        str: The actual Docker image name
    """
    try:
        if ' | ' in display_name:
            # Format: "Server Name | Image Name" - extract the image name
            parts = display_name.split(' | ')
            if len(parts) >= 2:
                image_name = parts[1].strip()
                
                # Handle multi-image format: "[MULTI] Group Name"
                if image_name.startswith('[MULTI] '):
                    # This shouldn't happen for single image calls, but handle it
                    return image_name.replace('[MULTI] ', '').strip()
                
                return image_name
        
        # If no pipe separator, assume it's already just the image name
        return display_name.strip()
    except Exception as e:
        current_app.logger.error(f"Error parsing image name from '{display_name}': {str(e)}")
        return display_name  # Return original if parsing fails


def get_instance_duration(challenge_id):
    """
    Get the instance duration for a challenge in seconds
    
    Args:
        challenge_id: Challenge ID
        
    Returns:
        int: Duration in seconds (default 900 = 15 minutes)
    """
    try:
        challenge = DockerChallenge.query.filter_by(id=challenge_id).first()
        if challenge and challenge.instance_duration:
            return challenge.instance_duration * 60  # Convert minutes to seconds
        return 900  # Default 15 minutes
    except:
        return 900  # Default 15 minutes


def create_compose_stack(docker, images, team, challenge_id, primary_service=None):
    """
    Create multiple containers for compose-based challenge
    
    Args:
        docker: DockerConfig instance
        images: List of Docker images to deploy
        team: Team identifier
        challenge_id: Challenge ID
        primary_service: Service name that should be marked as primary
    
    Returns:
        tuple: (stack_id, containers_info, primary_port, network_name)
    """
    try:
        team_hash = hashlib.md5(team.encode("utf-8")).hexdigest()[:10]
        timestamp_suffix = int(time.time())
        stack_id = f"challenge_{challenge_id}_{team_hash}"
        
        # Create shared network for this stack with timestamp to ensure uniqueness
        network_name = f"ctf_{stack_id}_{timestamp_suffix}"
        network_config = {
            "Name": network_name,
            "Driver": "bridge",
            "Internal": False,
            "Labels": {
                "ctf.stack_id": stack_id,
                "ctf.challenge_id": str(challenge_id),
                "ctf.team": team
            }
        }
        
        # Create network with retry logic
        headers = {'Content-Type': 'application/json'}
        network_response = None
        max_retries = 3
        
        for attempt in range(max_retries):
            try:
                network_response = do_request(docker, '/networks/create', 
                                            method='POST', 
                                            headers=headers,
                                            data=json.dumps(network_config),
                                            timeout=60)  # Increase timeout for network creation
                
                if network_response and network_response.status_code == 201:
                    break
                elif network_response and network_response.status_code == 409:
                    # Network already exists, try to remove it first
                    try:
                        current_app.logger.info(f"Network {network_name} already exists, attempting cleanup...")
                        delete_response = do_request(docker, f'/networks/{network_name}', method='DELETE', timeout=30)
                        if delete_response:
                            current_app.logger.info(f"Cleanup response: {delete_response.status_code}")
                        time.sleep(3)  # Wait longer for Docker to clean up
                        continue
                    except Exception as cleanup_error:
                        current_app.logger.warning(f"Network cleanup error: {str(cleanup_error)}")
                        pass
                elif attempt < max_retries - 1:
                    current_app.logger.info(f"Network creation attempt {attempt + 1} failed, retrying...")
                    time.sleep(2)  # Wait before retry
                    continue
                else:
                    break
            except Exception as e:
                current_app.logger.error(f"Network creation attempt {attempt + 1} error: {str(e)}")
                if attempt < max_retries - 1:
                    time.sleep(2)
                    continue
                else:
                    break
        
        if not network_response:
            raise Exception(f"Failed to create network '{network_name}': No response from Docker API after {max_retries} attempts. Check Docker connectivity.")
        elif network_response.status_code != 201:
            error_detail = ""
            try:
                if network_response.text:
                    error_detail = f" - {network_response.text}"
            except:
                pass
            raise Exception(f"Failed to create network '{network_name}': HTTP {network_response.status_code}{error_detail}")
        
        containers = []
        primary_port = None
        created_containers = []  # Track for cleanup on failure
        
        # Create each container
        for i, image in enumerate(images):
            # Extract service name from image (after last underscore)
            if '_' in image:
                service_name = image.split('_')[-1].split(':')[0]  # Remove tag if present
            else:
                service_name = image.split(':')[0]  # Use image name if no underscore
            
            container_name = f"{stack_id}_{service_name}"
            
            # Get ports for this image
            try:
                needed_ports = get_required_ports(docker, image)
            except Exception as e:
                current_app.logger.warning(f"Failed to get ports for {image}: {str(e)}")
                needed_ports = []  # Continue without ports if detection fails
            
            # Configure port bindings
            port_bindings = {}
            exposed_ports = {}
            container_ports = []
            
            for port in needed_ports:
                assigned_port = get_available_port(docker)
                port_bindings[f"{port}/tcp"] = [{"HostPort": str(assigned_port)}]
                exposed_ports[f"{port}/tcp"] = {}
                container_ports.append(assigned_port)
                
                # Mark primary service port
                if (service_name == primary_service or not primary_port) and needed_ports:
                    primary_port = assigned_port
            
            # Container configuration
            container_config = {
                "Image": image,
                "name": container_name,
                "ExposedPorts": exposed_ports,
                "NetworkingConfig": {
                    "EndpointsConfig": {
                        network_name: {
                            "Aliases": [service_name]  # Allow inter-container communication by service name
                        }
                    }
                },
                "HostConfig": {
                    "PortBindings": port_bindings,
                    "RestartPolicy": {"Name": "unless-stopped"},
                    "NetworkMode": network_name
                },
                "Labels": {
                    "ctf.managed": "true",
                    "ctf.type": "compose",
                    "ctf.stack_id": stack_id,
                    "ctf.challenge_id": str(challenge_id),
                    "ctf.team": team,
                    "ctf.service": service_name,
                    "ctf.image": image,
                    "ctf.is_primary": str(service_name == primary_service).lower(),
                    "ctf.created_at": str(int(time.time())),
                    "ctf.project_name": stack_id.split('_')[1] if '_' in stack_id else stack_id  # Extract challenge part
                }
            }
            
            # Get environment variables from challenge if they exist
            env_vars = []
            if challenge_id:
                try:
                    challenge = DockerChallenge.query.filter_by(id=challenge_id).first()
                    if challenge and challenge.environment_vars:
                        # Parse and validate environment variables
                        env_vars, errors = parse_environment_vars(challenge.environment_vars)
                        if errors:
                            current_app.logger.warning(f"Environment variable validation errors for challenge {challenge_id}: {', '.join(errors)}")
                except Exception as e:
                    current_app.logger.warning(f"Error parsing environment variables for challenge {challenge_id}: {str(e)}")
            
            # Add environment variables if any
            if env_vars:
                container_config["Env"] = env_vars
            
            # Create container
            create_response = do_request(docker, '/containers/create', 
                                       method='POST',
                                       headers=headers,
                                       data=json.dumps(container_config))
            
            if not create_response or create_response.status_code != 201:
                raise Exception(f"Failed to create container {container_name}: {create_response.status_code if create_response else 'No response'}")
            
            container_id = create_response.json()['Id']
            created_containers.append(container_id)
            
            # Start container
            start_response = do_request(docker, f'/containers/{container_id}/start', method='POST')
            if not start_response or start_response.status_code != 204:
                raise Exception(f"Failed to start container {container_name}: {start_response.status_code if start_response else 'No response'}")
            
            containers.append({
                'id': container_id,
                'name': container_name,
                'image': image,
                'service': service_name,
                'ports': container_ports,
                'is_primary': service_name == primary_service
            })
        
        current_app.logger.info(f"Successfully created compose stack {stack_id} with {len(containers)} containers")
        return stack_id, containers, primary_port, network_name
        
    except Exception as e:
        current_app.logger.error(f"Error creating compose stack: {str(e)}")
        # Cleanup on failure
        cleanup_failed_stack(docker, network_name if 'network_name' in locals() else None, 
                           created_containers if 'created_containers' in locals() else [])
        raise e


def migrate_existing_containers_labels(docker):
    """
    Migration helper to add challenge labels to existing containers
    This can be called manually by admins if needed
    """
    try:
        # Get all containers
        r = do_request(docker, '/containers/json?all=1')
        if not r or r.status_code != 200:
            return False
        
        containers = r.json()
        updated_count = 0
        
        for container in containers:
            container_id = container['Id']
            container_name = container.get('Names', [''])[0].lstrip('/')
            
            # Skip if already has CTF labels
            labels = container.get('Labels') or {}
            if 'ctf.managed' in labels:
                continue
            
            # Try to match with existing tracker entries
            tracker_entries = DockerChallengeTracker.query.filter_by(instance_id=container_id).all()
            if tracker_entries:
                for tracker in tracker_entries:
                    # Could add labels here via Docker API if needed
                    # This is complex and would require container recreation

                    updated_count += 1
        

        return True
        
    except Exception as e:
        current_app.logger.error(f"Error in migrate_existing_containers_labels: {str(e)}")
        return False


def cleanup_failed_stack(docker, network_name, container_ids):
    """
    Cleanup resources after failed stack creation
    """
    try:
        # Stop and remove containers
        for container_id in container_ids:
            try:
                do_request(docker, f'/containers/{container_id}/stop', method='POST')
                do_request(docker, f'/containers/{container_id}?force=true', method='DELETE')
            except Exception as e:
                current_app.logger.warning(f"Failed to cleanup container {container_id}: {str(e)}")
        
        # Remove network
        if network_name:
            try:
                do_request(docker, f'/networks/{network_name}', method='DELETE')
            except Exception as e:
                current_app.logger.warning(f"Failed to cleanup network {network_name}: {str(e)}")
    except Exception as e:
        current_app.logger.error(f"Error during stack cleanup: {str(e)}")


def delete_compose_stack(docker, stack_id):
    """
    Delete entire compose stack (all containers and network)
    
    Args:
        docker: DockerConfig instance
        stack_id: Stack identifier
    
    Returns:
        bool: Success status  
    """
    try:
        # Get all containers in this stack
        trackers = DockerChallengeTracker.query.filter_by(stack_id=stack_id).all()
        
        container_ids = []
        network_name = None
        
        for tracker in trackers:
            if tracker.instance_id:
                container_ids.append(tracker.instance_id)
            if tracker.network_name and not network_name:
                network_name = tracker.network_name
        
        # Stop and remove all containers
        for container_id in container_ids:
            try:
                # Stop container
                stop_response = do_request(docker, f'/containers/{container_id}/stop', method='POST')
                
                # Remove container
                remove_response = do_request(docker, f'/containers/{container_id}?force=true', method='DELETE')
                
                current_app.logger.info(f"Removed container {container_id} from stack {stack_id}")
            except Exception as e:
                current_app.logger.warning(f"Failed to remove container {container_id}: {str(e)}")
        
        # Remove network
        if network_name:
            try:
                network_response = do_request(docker, f'/networks/{network_name}', method='DELETE')
                current_app.logger.info(f"Removed network {network_name} for stack {stack_id}")
            except Exception as e:
                current_app.logger.warning(f"Failed to remove network {network_name}: {str(e)}")
        
        # Remove from database
        DockerChallengeTracker.query.filter_by(stack_id=stack_id).delete()
        db.session.commit()
        
        current_app.logger.info(f"Successfully deleted compose stack {stack_id}")
        return True
        
    except Exception as e:
        current_app.logger.error(f"Error deleting compose stack {stack_id}: {str(e)}")
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
        'create': '/plugins/docker_challenges/assets/create.js?v=20250924183200',
        'update': '/plugins/docker_challenges/assets/update.js',
        'view': '/plugins/docker_challenges/assets/view.js?v=20250925030000',
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
        
        # Handle docker_image field specially - it might contain server info
        # The frontend sends 'docker_selection' but we need to process it as 'docker_image'
        if 'docker_selection' in data:
            data['docker_image'] = data.pop('docker_selection')
        
        if 'docker_image' in data:
            docker_image_selection = data['docker_image']
            current_app.logger.info(f"Update: docker_image selection = {docker_image_selection}")
            
            server = None
            image_name = docker_image_selection
            server_name = None
            
            # Try to parse as JSON first (new frontend format)
            try:
                if docker_image_selection and docker_image_selection.startswith('{'):
                    import json
                    selection_data = json.loads(docker_image_selection)
                    server_name = selection_data.get('server_name')
                    image_name = selection_data.get('image_name') or selection_data.get('name', '').replace(f"{server_name} | ", "")
                    current_app.logger.info(f"JSON format: server={server_name}, image={image_name}")
                elif ' | ' in docker_image_selection:
                    # Legacy format: "ServerName | ImageName"
                    server_name, image_name = docker_image_selection.split(' | ', 1)
                    current_app.logger.info(f"Legacy format: server={server_name}, image={image_name}")
                else:
                    # Just image name
                    image_name = docker_image_selection
                    server_name = None
                    current_app.logger.info(f"Plain image name: {image_name}")
            except (json.JSONDecodeError, ValueError) as e:
                current_app.logger.error(f"Error parsing docker_image: {e}")
                # Fallback to string parsing
                if ' | ' in docker_image_selection:
                    server_name, image_name = docker_image_selection.split(' | ', 1)
                else:
                    image_name = docker_image_selection
                    server_name = None
            
            # Find the server
            if server_name:
                server = DockerConfig.query.filter_by(name=server_name, is_active=True).first()
                if server:
                    current_app.logger.info(f"Found server: {server.name} (ID: {server.id})")
                    challenge.docker_config_id = server.id
                else:
                    current_app.logger.warning(f"Server '{server_name}' not found or inactive")
            else:
                # If no server specified, try to keep existing server or find one
                if not challenge.docker_config_id:
                    server = DockerConfig.query.filter_by(is_active=True).first()
                    if server:
                        current_app.logger.info(f"Using first available server: {server.name}")
                        challenge.docker_config_id = server.id
                else:
                    server = DockerConfig.query.filter_by(id=challenge.docker_config_id).first()
                    current_app.logger.info(f"Keeping existing server ID: {challenge.docker_config_id}")
            
            # Check if this is a multi-image challenge
            if image_name.startswith('[MULTI] '):
                # Multi-image challenge
                group_name = image_name.replace('[MULTI] ', '')
                challenge.challenge_type = 'multi'
                challenge.docker_image = group_name
                current_app.logger.info(f"Multi-image challenge: {group_name}")
                
                # Get the images for this group from server repositories
                if server:
                    try:
                        repositories = get_repositories(server, group_compose=True)
                        for repo_data in repositories:
                            if repo_data.get('type') == 'compose_group' and repo_data.get('name') == group_name:
                                challenge.docker_images = repo_data.get('images', [])
                                challenge.primary_service = repo_data.get('primary_service')
                                current_app.logger.info(f"Set docker_images: {challenge.docker_images}")
                                break
                    except Exception as e:
                        current_app.logger.warning(f"Could not get compose group data during update: {str(e)}")
            else:
                # Single image challenge
                challenge.challenge_type = 'single'
                challenge.docker_image = image_name
                challenge.docker_images = None
                challenge.primary_service = None
                current_app.logger.info(f"Single image challenge: {image_name}")
        
        # Update other attributes normally, excluding docker_image (already handled)
        for attr, value in data.items():
            if attr != 'docker_image':  # Already processed above
                # Only update if attribute exists on the model
                if hasattr(challenge, attr):
                    setattr(challenge, attr, value)

        # Ensure changes are committed
        db.session.flush()
        db.session.commit()
        db.session.refresh(challenge)
        
        current_app.logger.info(f"Challenge updated - ID: {challenge.id}, docker_image: {challenge.docker_image}, docker_config_id: {challenge.docker_config_id}")
        
        return challenge

    @staticmethod
    def delete(challenge):
        """
		This method is used to delete the resources used by a challenge.
		NOTE: Will need to kill all containers here

		:param challenge:
		:return:
		"""
        # Delete all running containers for this challenge first
        try:
            docker_containers = DockerChallengeTracker.query.filter_by(challenge=challenge.name).all()
            for container in docker_containers:
                try:
                    if container.docker_config:
                        if container.stack_id:
                            # Multi-image challenge - delete entire stack
                            delete_compose_stack(container.docker_config, container.stack_id)
                        else:
                            # Single container
                            delete_container(container.docker_config, container.instance_id)
                except Exception as e:
                    current_app.logger.error(f"Error deleting container {container.instance_id}: {str(e)}")
            # Remove all containers from tracker
            DockerChallengeTracker.query.filter_by(challenge=challenge.name).delete()
        except Exception as e:
            current_app.logger.error(f"Error cleaning up docker containers: {str(e)}")
        
        # Delete anti_cheat_alerts if the plugin is loaded
        try:
            # Import here to avoid dependency issues if anti_cheat plugin is not loaded
            from CTFd.plugins.anti_cheat import AntiCheatAlert
            AntiCheatAlert.query.filter_by(challenge_id=challenge.id).delete()
        except ImportError:
            # Anti-cheat plugin not loaded, skip
            pass
        except Exception as e:
            current_app.logger.error(f"Error deleting anti_cheat_alerts: {str(e)}")
        
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
		Now includes enhanced fields for multi-image and web/tcp support.

		:param challenge:
		:return: Challenge object, data dictionary to be returned to the user
		"""
        challenge = DockerChallenge.query.filter_by(id=challenge.id).first()
        
        # Format docker_image with server name for dropdown to display correctly
        docker_image_display = challenge.docker_image
        if challenge.docker_config:
            # Include server name in the format that the dropdown expects
            if challenge.challenge_type == 'multi':
                docker_image_display = f"{challenge.docker_config.name} | [MULTI] {challenge.docker_image}"
            else:
                docker_image_display = f"{challenge.docker_config.name} | {challenge.docker_image}"
        
        data = {
            'id': challenge.id,
            'name': challenge.name,
            'value': challenge.value,
            'docker_image': docker_image_display,  # Use formatted version for dropdown
            'docker_config_id': challenge.docker_config_id,
            'server_name': challenge.docker_config.name if challenge.docker_config else 'Unknown Server',
            'description': challenge.description,
            'category': challenge.category,
            'state': challenge.state,
            'max_attempts': challenge.max_attempts,
            'type': challenge.type,
            # CTFd 3.8.0 compatibility - include logic field if it exists
            'logic': getattr(challenge, 'logic', 'any'),
            
            # New enhanced fields
            'challenge_type': getattr(challenge, 'challenge_type', 'single'),
            'docker_images': getattr(challenge, 'docker_images', None),
            'primary_service': getattr(challenge, 'primary_service', None),
            'connection_type': getattr(challenge, 'connection_type', 'tcp'),
            'instance_duration': getattr(challenge, 'instance_duration', 15),
            'custom_subdomain': getattr(challenge, 'custom_subdomain', None),
            'environment_vars': getattr(challenge, 'environment_vars', None),
            
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
        
        # Handle the docker image selection - could be string or JSON
        docker_image_selection = data.get('docker_image', '')
        
        # Try to parse as JSON first (new frontend format)
        try:
            if docker_image_selection.startswith('{'):
                import json
                selection_data = json.loads(docker_image_selection)
                server_name = selection_data.get('server_name')
                image_name = selection_data.get('image_name') or selection_data.get('name', '').replace(f"{server_name} | ", "")
            elif ' | ' in docker_image_selection:
                # Legacy format: "ServerName | ImageName"
                server_name, image_name = docker_image_selection.split(' | ', 1)
            else:
                # Very old format - just image name
                image_name = docker_image_selection
                server_name = None
        except (json.JSONDecodeError, ValueError) as e:
            # Fallback to string parsing
            if ' | ' in docker_image_selection:
                server_name, image_name = docker_image_selection.split(' | ', 1)
            else:
                image_name = docker_image_selection
                server_name = None
        
        # Find the server
        if server_name:
            server = DockerConfig.query.filter_by(name=server_name, is_active=True).first()
            if not server:
                raise Exception(f"Server '{server_name}' not found or inactive")
        else:
            # Find best server for the image
            server = get_best_server_for_image(image_name)
            if not server:
                server = DockerConfig.query.filter_by(is_active=True).first()
                if not server:
                    raise Exception("No active Docker servers available")
        
        # Filter to only include valid DockerChallenge model fields
        # These are the actual database columns from the DockerChallenge model
        valid_fields = {
            # Base Challenge fields
            'name', 'category', 'description', 'value', 'state', 'max_attempts',
            # DockerChallenge specific fields (matching the model exactly)
            'docker_image', 'docker_config_id', 'challenge_type', 'docker_images', 
            'primary_service', 'connection_type', 'instance_duration', 'custom_subdomain', 'environment_vars'
        }
        challenge_data = {k: v for k, v in data.items() if k in valid_fields}
        challenge_data['type'] = 'docker'  # Set the challenge polymorphic type
        
        # Check if this is a multi-image challenge
        if image_name.startswith('[MULTI] '):
            # Multi-image challenge
            group_name = image_name.replace('[MULTI] ', '')
            challenge_data['challenge_type'] = 'multi'
            challenge_data['docker_image'] = group_name  # Store group name for backward compatibility
            challenge_data['docker_config_id'] = server.id
            
            # Get the images for this group from server repositories
            try:
                repositories = get_repositories(server, group_compose=True)
                for repo_data in repositories:
                    if repo_data.get('type') == 'compose_group' and repo_data.get('name') == group_name:
                        challenge_data['docker_images'] = repo_data.get('images', [])
                        challenge_data['primary_service'] = repo_data.get('primary_service')
                        break
            except Exception as e:
                current_app.logger.warning(f"Could not get compose group data: {str(e)}")
        else:
            # Single image challenge
            challenge_data['challenge_type'] = 'single'
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
		return a ChallengeResponse object. It is also in charge of parsing the
		user's input from the request itself.

		:param challenge: The Challenge object from the database
		:param request: The request the user submitted
		:return: ChallengeResponse object
		"""

        data = request.form or request.get_json()

        submission = data["submission"].strip()
        flags = Flags.query.filter_by(challenge_id=challenge.id).all()
        for flag in flags:
            if get_flag_class(flag.type).compare(flag, submission):
                return ChallengeResponse(
                    status="correct",
                    message="Correct"
                )
        return ChallengeResponse(
            status="incorrect", 
            message="Incorrect"
        )

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
            current_app.logger.warning(f"Error cleaning up container on solve: {str(e)}")
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
    docker_image = db.Column(db.String(128), index=True)  # Keep for backward compatibility
    docker_config_id = db.Column("docker_config_id", db.Integer, db.ForeignKey('docker_config.id'), index=True)  # Which server to use
    
    # New fields for enhanced functionality
    challenge_type = db.Column("challenge_type", db.String(32), default='single')  # 'single' or 'multi'
    docker_images = db.Column("docker_images", db.JSON, nullable=True)  # Array of images for multi-image challenges
    primary_service = db.Column("primary_service", db.String(64), nullable=True)  # Which service is primary for multi-image
    connection_type = db.Column("connection_type", db.String(32), default='tcp')  # 'tcp' or 'web'
    instance_duration = db.Column("instance_duration", db.Integer, default=15)  # Duration in minutes
    custom_subdomain = db.Column("custom_subdomain", db.String(128), nullable=True)  # Optional custom subdomain
    environment_vars = db.Column("environment_vars", db.Text, nullable=True)  # Environment variables (KEY=VALUE format, one per line)
    
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
            container_display = request.args.get('name')
            
            if not container_display:
                return {"success": False, "message": "No container specified"}, 403
            
            # Check if this is a multi-image selection (display format contains indicators)
            is_multi_image = False
            actual_images = []
            project_name = None
            
            # Detect multi-image format: "[MULTI] project_name (X images)"
            if container_display.startswith('[MULTI] ') or '(2 images)' in container_display or '(3 images)' in container_display:
                is_multi_image = True
                current_app.logger.info(f"✓ Detected multi-image container: {container_display}")
                
                # Extract project name - remove [MULTI], count info, and [Unlabeled] parts
                project_name = container_display
                project_name = project_name.replace('[MULTI] ', '')
                project_name = project_name.split(' (')[0]  # Remove "(X images) [Unlabeled]" part
                project_name = project_name.replace(' [Unlabeled]', '')  # Remove [Unlabeled] if present
                project_name = project_name.strip()
                current_app.logger.info(f"✓ Extracted project name: '{project_name}'")
            else:
                # Parse the actual image name from the display format
                container = parse_image_name_from_display(container_display)
                
                # Basic input validation
                if not isinstance(container, str) or len(container) > 256:
                    return {"success": False, "message": "Invalid container name"}, 400
                

                
            challenge = request.args.get('challenge')
            if not challenge:
                return {"success": False, "message": "No challenge name specified"}, 403
                
            # Basic input validation
            if not isinstance(challenge, str) or len(challenge) > 256:
                return {"success": False, "message": "Invalid challenge name"}, 400
            
            # Get challenge ID from challenge name - use Challenges table, not DockerChallenge
            from CTFd.models import Challenges
            challenge_obj = Challenges.query.filter_by(name=challenge).first()
            if not challenge_obj:
                return {"success": False, "message": f"Challenge '{challenge}' not found"}, 404
            challenge_id = challenge_obj.id
            
            # Get DockerChallenge info if this is a docker challenge
            docker_challenge_obj = None
            if challenge_obj.type == 'docker':
                docker_challenge_obj = DockerChallenge.query.filter_by(id=challenge_id).first()
            
            # Find the best server for this container image/group
            if is_multi_image:
                # For multi-image, we need to find a server and get the actual images
                servers = DockerConfig.query.filter_by(is_active=True).all()
                docker = None
                
                for server in servers:
                    try:
                        # Get repositories with compose grouping to find the actual images
                        repositories = get_repositories(server, group_compose=True, challenge_id=challenge_id)
                        
                        # Look for the compose group matching our project name
                        for repo_item in repositories:
                            if isinstance(repo_item, dict) and repo_item.get('type') == 'compose_group':
                                # Match by project name, name, or display name
                                if (repo_item.get('name') == project_name or 
                                    repo_item.get('project_name') == project_name or 
                                    repo_item.get('display_name', '').startswith(project_name + ' (')):
                                    actual_images = repo_item.get('images', [])
                                    docker = server
                                    current_app.logger.info(f"Found matching compose group: {repo_item.get('display_name')} with images: {actual_images}")
                                    break
                        
                        if actual_images:
                            break
                    except Exception as e:
                        current_app.logger.error(f"Error checking server {server.name}: {str(e)}")
                        continue
                
                if not docker or not actual_images:
                    current_app.logger.error(f"Multi-image detection failed: docker={docker}, actual_images={actual_images}, project_name='{project_name}'")
                    return {"success": False, "message": f"No server found with multi-image group '{project_name}' or no images found"}, 500
            else:
                # Single image - use original logic
                docker = get_best_server_for_image(container)
                if not docker:
                    return {"success": False, "message": f"No available Docker server found for image: {container}"}, 500
                
                # Check if container exists in repository (skip if we can't get repo list)
                try:
                    repositories = get_repositories(docker, tags=True)
                    if container not in repositories:
                        current_app.logger.info(f"Container {container} not found in repository list, will attempt to pull")
                        # Don't abort here - let Docker try to pull the image
                except Exception as e:
                    current_app.logger.warning(f"Could not get repository list from server {docker.name}: {str(e)}")
                    current_app.logger.info("Continuing anyway - Docker will attempt to pull image if needed")
                    # Don't abort here - continue with the container operation
            
            # Get current session
            try:
                if is_teams_mode():
                    session = get_current_team()
                else:
                    session = get_current_user()
                    
                if not session:
                    return {"success": False, "message": "No valid session"}, 403
            except Exception as e:
                current_app.logger.error(f"Error getting session: {str(e)}")
                import traceback
                traceback.print_exc()
                return {"success": False, "message": "Failed to get user session"}, 500
            
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
                                current_app.logger.error(f"Error removing old team container: {str(e)}")
                    else:
                        if i.user_id is not None and int(session.id) == int(i.user_id) and container_age >= 7200:
                            try:
                                if i.docker_config:
                                    delete_container(i.docker_config, i.instance_id)
                                DockerChallengeTracker.query.filter_by(instance_id=i.instance_id).delete()
                                db.session.commit()
                            except Exception as e:
                                current_app.logger.error(f"Error removing old user container: {str(e)}")
            except Exception as e:
                current_app.logger.error(f"Error during old container cleanup: {str(e)}")
                import traceback
                traceback.print_exc()
            
            # Check for existing container for this specific image/challenge
            # Also implement a basic rate limiting (minimum 30 seconds between requests)
            
            # Note: Removed one-challenge-per-team restriction to allow multiple concurrent challenges
            # Teams can now run multiple challenge instances simultaneously
            
            # Now check for existing container for this specific challenge
            try:
                if is_multi_image:
                    # For multi-image, check if any container for this challenge exists
                    if is_teams_mode():
                        check = DockerChallengeTracker.query.filter_by(team_id=session.id, challenge=challenge).first()
                    else:
                        check = DockerChallengeTracker.query.filter_by(user_id=session.id, challenge=challenge).first()
                else:
                    # For single image, check specific image
                    if is_teams_mode():
                        check = DockerChallengeTracker.query.filter_by(team_id=session.id).filter_by(docker_image=container).first()
                    else:
                        check = DockerChallengeTracker.query.filter_by(user_id=session.id).filter_by(docker_image=container).first()
                
                # Check if user is making requests too frequently
                if check and (unix_time(datetime.utcnow()) - int(check.timestamp)) < 30:
                    return {"success": False, "message": "Rate limit exceeded. Please wait at least 30 seconds between requests."}, 429
                    
            except Exception as e:
                current_app.logger.error(f"Error checking existing container: {str(e)}")
                import traceback
                traceback.print_exc()
                check = None
            
            # If this container is already created, check what action is requested
            instance_duration = get_instance_duration(challenge_id)
            
            # Delete when requested (CHECK THIS FIRST, before checking expiration)
            if check != None and request.args.get('stopcontainer'):
                try:
                    if is_multi_image or (hasattr(check, 'stack_id') and check.stack_id):
                        # Multi-image challenge - delete entire stack
                        if check.stack_id and check.docker_config:
                            delete_compose_stack(check.docker_config, check.stack_id)
                        # Delete all containers for this challenge/stack
                        if is_teams_mode():
                            DockerChallengeTracker.query.filter_by(team_id=session.id, challenge=challenge).delete()
                        else:
                            DockerChallengeTracker.query.filter_by(user_id=session.id, challenge=challenge).delete()
                    else:
                        # Single container - get the actual image name
                        container_image = parse_image_name_from_display(container_display)
                        if check.docker_config:
                            delete_container(check.docker_config, check.instance_id)
                        if is_teams_mode():
                            DockerChallengeTracker.query.filter_by(team_id=session.id, challenge=challenge).delete()
                        else:
                            DockerChallengeTracker.query.filter_by(user_id=session.id, challenge=challenge).delete()
                    
                    db.session.commit()
                    return {"success": True, "result": "Container(s) stopped"}
                except Exception as e:
                    current_app.logger.error(f"Error stopping container: {str(e)}")
                    import traceback
                    traceback.print_exc()
                    return {"success": False, "message": "Failed to stop container"}, 500
            # The exception would be if we are reverting a box. So we'll delete it if it exists and has been around for more than the configured duration.
            elif check != None:
                try:
                    if is_multi_image or (hasattr(check, 'stack_id') and check.stack_id):
                        # Multi-image challenge - delete entire stack for revert
                        if check.stack_id and check.docker_config:
                            delete_compose_stack(check.docker_config, check.stack_id)
                        # Delete all containers for this challenge/stack
                        if is_teams_mode():
                            DockerChallengeTracker.query.filter_by(team_id=session.id, challenge=challenge).delete()
                        else:
                            DockerChallengeTracker.query.filter_by(user_id=session.id, challenge=challenge).delete()
                    else:
                        # Single container - get the actual image name for revert
                        container_image = parse_image_name_from_display(container_display)
                        if check.docker_config:
                            delete_container(check.docker_config, check.instance_id)
                        if is_teams_mode():
                            DockerChallengeTracker.query.filter_by(team_id=session.id, challenge=challenge).delete()
                        else:
                            DockerChallengeTracker.query.filter_by(user_id=session.id, challenge=challenge).delete()
                    
                    db.session.commit()
                except Exception as e:
                    current_app.logger.error(f"Error deleting existing container: {str(e)}")
                    import traceback
                    traceback.print_exc()
            
            # If container exists and is not expired, and no stop/revert was requested, return existing container info
            elif check != None and not (unix_time(datetime.utcnow()) - int(check.timestamp)) >= instance_duration:
                # Container exists and is not expired - return existing container info
                # Use custom subdomain if set in challenge, otherwise use docker server domain
                display_host = check.docker_config.domain if check.docker_config and check.docker_config.domain else str(check.host)
                if docker_challenge_obj and docker_challenge_obj.custom_subdomain:
                    display_host = docker_challenge_obj.custom_subdomain
                port_list = check.ports.split(',') if check.ports else []
                return {
                    "success": True,
                    "result": "Container already running",
                    "hostname": display_host,
                    "port": port_list[0] if port_list else None,
                    "revert_time": check.revert_time,
                    "existing": True
                }
            
            # Check if a container is already running for this user. We need to recheck the DB first
            # Also clean up any expired containers (older than configured duration)
            containers = DockerChallengeTracker.query.all()
            containers_to_remove = []
            
            for i in containers:
                # Check if container has expired based on challenge-specific duration
                current_time = unix_time(datetime.utcnow())
                container_age = current_time - int(i.timestamp)
                
                # Get duration from challenge, default to 15 minutes if not found
                instance_duration = 900  # Default 15 minutes
                if i.challenge:
                    try:
                        challenge_config = DockerChallenge.query.filter_by(name=i.challenge).first()
                        if challenge_config and challenge_config.instance_duration:
                            instance_duration = challenge_config.instance_duration * 60
                    except:
                        pass  # Use default if challenge lookup fails
                
                if container_age >= instance_duration:
                    try:
                        if i.docker_config:
                            delete_container(i.docker_config, i.instance_id)
                        containers_to_remove.append(i)
                    except Exception as e:
                        current_app.logger.error(f"Error deleting expired container {i.instance_id}: {str(e)}")
                        # Only remove from DB if Docker deletion was successful
                        continue
                    continue
                
                # This logic is now handled earlier in the function - no need for duplicate check
            
            # Remove expired containers from database
            for container_obj in containers_to_remove:
                try:
                    DockerChallengeTracker.query.filter_by(instance_id=container_obj.instance_id).delete()
                    db.session.commit()
                except Exception as e:
                    current_app.logger.error(f"Error removing expired container from DB: {str(e)}")

            # Check if this is a multi-image selection or challenge
            if is_multi_image or (docker_challenge_obj and docker_challenge_obj.challenge_type == 'multi' and docker_challenge_obj.docker_images):
                # Multi-image challenge - use compose stack creation
                try:
                    # Use actual images from server if we detected multi-image selection
                    # Otherwise use challenge configuration
                    images_to_use = actual_images if is_multi_image else docker_challenge_obj.docker_images
                    primary_service = docker_challenge_obj.primary_service if docker_challenge_obj else None
                    
                    current_app.logger.info(f"Creating multi-image stack with images: {images_to_use}")
                    current_app.logger.info(f"Primary service: {primary_service}")
                    
                    stack_id, containers, primary_port, network_name = create_compose_stack(
                        docker=docker,
                        images=images_to_use,
                        team=session.name,
                        challenge_id=challenge_id,
                        primary_service=primary_service
                    )
                    
                    current_app.logger.info(f"Multi-image stack created successfully: stack_id={stack_id}, containers={len(containers)}")
                    
                    # Create tracker entries for all containers in the stack
                    # Use custom subdomain if set in challenge, otherwise use docker server domain
                    display_host = docker.domain if docker.domain else str(docker.hostname).split(':')[0]
                    if docker_challenge_obj and docker_challenge_obj.custom_subdomain:
                        display_host = docker_challenge_obj.custom_subdomain
                    
                    for container_info in containers:
                        entry = DockerChallengeTracker(
                            team_id=session.id if is_teams_mode() else None,
                            user_id=session.id if not is_teams_mode() else None,
                            docker_image=container_info['image'],
                            timestamp=unix_time(datetime.utcnow()),
                            revert_time=unix_time(datetime.utcnow()) + instance_duration,
                            instance_id=container_info['id'],
                            ports=','.join([str(p) for p in container_info['ports']]),
                            host=display_host,
                            challenge=challenge,
                            docker_config_id=docker.id,
                            stack_id=stack_id,
                            service_name=container_info['service'],
                            is_primary=container_info['is_primary'],
                            network_name=network_name
                        )
                        db.session.add(entry)
                    
                    db.session.commit()
                    
                    # Return connection info for the primary service
                    primary_container = next((c for c in containers if c['is_primary']), containers[0])
                    response_data = {
                        "success": True,
                        "result": f"Container stack created successfully with {len(containers)} containers",
                        "hostname": display_host,
                        "port": primary_container['ports'][0] if primary_container['ports'] else None,
                        "revert_time": unix_time(datetime.utcnow()) + instance_duration
                    }
                    current_app.logger.info(f"Returning multi-container response: {response_data}")
                    return response_data
                except Exception as e:
                    current_app.logger.error(f"Error creating multi-container stack: {str(e)}")
                    import traceback
                    current_app.logger.error(f"Full traceback: {traceback.format_exc()}")
                    return {"success": False, "message": f"Failed to create container stack: {str(e)}"}, 500

            # Single image challenge - use original logic
            if not is_multi_image:
                # Get ports and create container
                try:
                    portsbl = get_unavailable_ports(docker)
                    
                    create = create_container(docker, container, session.name, portsbl, challenge_id=challenge_id)
                    
                    ports = json.loads(create[1])['HostConfig']['PortBindings'].values()
                    
                    # Determine what host/domain to show to user
                    # Use custom subdomain if set in challenge, otherwise use docker server domain
                    display_host = docker.domain if docker.domain else str(docker.hostname).split(':')[0]
                    if docker_challenge_obj and docker_challenge_obj.custom_subdomain:
                        display_host = docker_challenge_obj.custom_subdomain
                    
                    port_list = [p[0]['HostPort'] for p in ports]
                    entry = DockerChallengeTracker(
                        team_id=session.id if is_teams_mode() else None,
                        user_id=session.id if not is_teams_mode() else None,
                        docker_image=container,
                        timestamp=unix_time(datetime.utcnow()),
                        revert_time=unix_time(datetime.utcnow()) + instance_duration,
                        instance_id=create[0]['Id'],
                        ports=','.join(port_list),
                        host=display_host,
                        challenge=challenge,
                        docker_config_id=docker.id
                    )
                    db.session.add(entry)
                    db.session.commit()
                    return {
                        "success": True,
                        "result": "Container created successfully",
                        "hostname": display_host,
                        "port": port_list[0] if port_list else None,
                        "revert_time": unix_time(datetime.utcnow()) + instance_duration
                    }
                except Exception as e:
                    current_app.logger.error(f"Error creating container: {str(e)}")
                    import traceback
                    traceback.print_exc()
                    return {"success": False, "message": f"Failed to create container: {str(e)}"}, 500
        
        except Exception as e:
            current_app.logger.error(f"Error in ContainerAPI.get(): {str(e)}")
            import traceback
            traceback.print_exc()
            return {"success": False, "message": f"Internal server error: {str(e)}"}, 500


active_docker_namespace = Namespace("docker_status", description='Endpoint to retrieve User Docker Image Status')


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
            
            # Get duration from challenge, default to 15 minutes if not found
            instance_duration = 900  # Default 15 minutes
            if container.challenge:
                try:
                    challenge = DockerChallenge.query.filter_by(name=container.challenge).first()
                    if challenge and challenge.instance_duration:
                        instance_duration = challenge.instance_duration * 60
                except:
                    pass  # Use default if challenge lookup fails
            
            if container_age >= instance_duration:
                try:
                    if container.docker_config:
                        delete_container(container.docker_config, container.instance_id)
                    global_containers_to_remove.append(container)
                except Exception as e:
                    current_app.logger.error(f"Error deleting expired container {container.instance_id}: {str(e)}")
                    # Still remove from DB even if Docker deletion fails
                    global_containers_to_remove.append(container)
        
        # Remove expired containers from database
        for container in global_containers_to_remove:
            try:
                DockerChallengeTracker.query.filter_by(instance_id=container.instance_id).delete()
                db.session.commit()
            except Exception as e:
                current_app.logger.error(f"Error removing expired container from DB: {str(e)}")
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
            # Check if container has expired based on challenge-specific duration
            instance_duration = 900  # Default 15 minutes
            if i.challenge:
                try:
                    challenge = DockerChallenge.query.filter_by(name=i.challenge).first()
                    if challenge and challenge.instance_duration:
                        instance_duration = challenge.instance_duration * 60
                except:
                    pass  # Use default if challenge lookup fails
            
            if (unix_time(datetime.utcnow()) - int(i.timestamp)) >= instance_duration:
                try:
                    if i.docker_config:
                        delete_container(i.docker_config, i.instance_id)
                    containers_to_remove.append(i)
                except Exception as e:
                    current_app.logger.error(f"Error deleting expired container {i.instance_id}: {str(e)}")
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
                'server_name': i.docker_config.name if i.docker_config else 'Unknown Server',
                'challenge_name': i.challenge,
                'is_primary': i.is_primary if hasattr(i, 'is_primary') else False
            })
        
        # Remove expired containers from database
        for container in containers_to_remove:
            try:
                DockerChallengeTracker.query.filter_by(instance_id=container.instance_id).delete()
                db.session.commit()
            except Exception as e:
                current_app.logger.error(f"Error removing expired container from DB: {str(e)}")
        
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
            current_app.logger.info("DockerAPI.get() called")

            # Check if challenge_id is provided for filtering
            challenge_id = request.args.get('challenge_id')
            servers = DockerConfig.query.filter_by(is_active=True).all()
            current_app.logger.info(f"Found {len(servers)} active servers")
            
            if not servers:
                current_app.logger.error("No active Docker servers configured")
                return {
                    'success': False,
                    'data': [{'name': 'Error: No Docker servers configured!'}]
                }, 400
            

            data = []
            
            for server in servers:
                try:
                    current_app.logger.info(f"Processing server: {server.name} ({server.hostname})")
                    
                    # Check TLS configuration first
                    if server.tls_enabled:
                        if not server.ca_cert or not server.client_cert or not server.client_key:
                            current_app.logger.error(f"Server {server.name} has TLS enabled but missing certificates")
                            data.append({
                                'name': f'⚠️ {server.name}',
                                'options': [{'name': f'Error: {server.name} - Missing TLS certificates. Please upload CA, client certificate, and client key.'}]
                            })
                            continue
                    
                    # Convert repositories string to list if it exists
                    server_repos = None
                    if server.repositories:
                        server_repos = server.repositories.split(',')
                        current_app.logger.info(f"Server {server.name} allowed repos: {server_repos}")
                    
                    # Get both single images and compose groups
                    current_app.logger.info(f"Calling get_repositories for server {server.name}")
                    images = get_repositories(server, tags=True, repos=server_repos, group_compose=True, challenge_id=challenge_id)
                    current_app.logger.info(f"get_repositories returned {len(images) if images else 0} items for server {server.name}")
                    current_app.logger.info(f"Images from {server.name}: {images}")

                    if images:
                        for item in images:

                            if isinstance(item, dict) and item.get('type') == 'compose_group':
                                # Multi-image compose group

                                display_name = f"{server.name} | [MULTI] {item['display_name']}"
                                data.append({
                                    'name': display_name,
                                    'server_id': server.id,
                                    'server_name': server.name,
                                    'type': 'multi',
                                    'project_name': item['name'],
                                    'images': item['images'],
                                    'services': item['services'],
                                    'image_count': item['image_count'],
                                    'server_domain': server.domain,
                                    'challenge_id': item.get('challenge_id'),
                                    'is_labeled': item.get('is_labeled', False),
                                    'group_key': item.get('group_key')
                                })
                            else:
                                # Single image (existing logic)
                                display_name = f"{server.name} | {item}"
                                data.append({
                                    'name': display_name,
                                    'server_id': server.id,
                                    'server_name': server.name,
                                    'type': 'single',
                                    'image_name': item,
                                    'server_domain': server.domain
                                })
                except Exception as e:
                    current_app.logger.error(f"Error getting images from server {server.name}: {str(e)}")
                    import traceback
                    current_app.logger.error(f"Full traceback: {traceback.format_exc()}")
                    
                    # Provide specific error messages based on the error type
                    error_msg = str(e)
                    if "SSL" in error_msg or "TLS" in error_msg or "certificate" in error_msg.lower():
                        error_msg = "TLS Certificate Error - Check certificates"
                    elif "Connection" in error_msg or "timeout" in error_msg.lower():
                        error_msg = "Connection Error - Server unreachable"
                    else:
                        error_msg = f"Error: {str(e)[:50]}..."
                    
                    # Add error entry for this server
                    data.append({
                        'name': f"⚠️ {server.name}",
                        'options': [{'name': f'{server.name} - {error_msg}'}],
                        'type': 'error',
                        'error': True
                    })
            
            if not data:
                return {
                    'success': False,
                    'data': [{'name': 'Error: No images found on any server!'}]
                }, 400
            
            for item in data:
                pass  # Placeholder for any post-processing logic
            
            return {
                'success': True,
                'data': data
            }
            
        except Exception as e:
            current_app.logger.error(f"Error in DockerAPI: {str(e)}")
            return {
                'success': False,
                'data': [{'name': f'Error: {str(e)}'}]
            }, 500



def load(app):
    # Run migrations first
    try:
        upgrade(plugin_name="docker_challenges")
        current_app.logger.info("Docker challenges migrations completed successfully")
    except Exception as e:
        current_app.logger.error(f"Migration failed: {str(e)}")
        current_app.logger.info("Attempting manual database creation...")
        
    # Create tables if they don't exist
    try:
        app.db.create_all()
        current_app.logger.info("Database tables created/verified successfully")
    except Exception as e:
        current_app.logger.error(f"Error creating database tables: {str(e)}")
    
    # Run database migration for legacy data
    try:
        migrate_old_config()
    except Exception as e:
        current_app.logger.error(f"Legacy data migration failed: {str(e)}")
        current_app.logger.warning("Plugin will continue loading but some features may not work correctly")
    
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
        current_app.logger.info("Docker challenges plugin loaded with multi-server support and background cleanup")
    except Exception as e:
        current_app.logger.error(f"Error starting cleanup thread: {str(e)}")
        current_app.logger.warning("Plugin loaded but background cleanup is disabled")


def migrate_old_config():
    """
    Migrate old single-server configuration to new multi-server format
    """
    try:
        # First, check if we need to add new columns to existing table
        from sqlalchemy import text
        
        current_app.logger.info("Checking database schema for Docker plugin...")
        
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
                    current_app.logger.info(f"Adding missing column: {column_name}")
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
                    
                    # Check if container has expired based on challenge-specific duration
                    instance_duration = 900  # Default 15 minutes
                    if container.challenge:
                        try:
                            challenge = DockerChallenge.query.filter_by(name=container.challenge).first()
                            if challenge and challenge.instance_duration:
                                instance_duration = challenge.instance_duration * 60
                        except:
                            pass  # Use default if challenge lookup fails
                    
                    if container_age >= instance_duration:
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
