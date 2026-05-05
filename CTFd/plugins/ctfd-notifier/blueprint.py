import json
from urllib.parse import urlparse

import requests as rq
import tweepy
from CTFd.utils.decorators import admins_only
from flask import Blueprint, jsonify, render_template, request

from .db_utils import DBUtils

notifier_bp = Blueprint("notifier", __name__, template_folder="templates")


def load_bp(plugin_route):
    @notifier_bp.route(plugin_route, methods=["GET"])
    @admins_only
    def get_config():
        config = DBUtils.get_config()
        return render_template("ctfd_notifier/config.html", config=config, errors=[])

    @notifier_bp.route(plugin_route, methods=["POST"])
    @admins_only
    def update_config():
        config = request.form.to_dict()
        del config["nonce"]

        errors = test_config(config)

        if len(errors) > 0:
            return render_template(
                "ctfd_notifier/config.html", config=DBUtils.get_config(), errors=errors
            )
        else:
            DBUtils.save_config(config.items())
            return render_template(
                "ctfd_notifier/config.html", config=DBUtils.get_config(), errors=[]
            )

    @notifier_bp.route("/admin/notifier/test_discord", methods=["POST"])
    @admins_only
    def test_discord():
        data = request.get_json(silent=True) or {}
        webhookurl = data.get("webhook_url", "").strip()

        try:
            parsed = urlparse(webhookurl)
            valid = (
                parsed.scheme == "https"
                and parsed.hostname in ("discord.com", "discordapp.com")
                and parsed.path.startswith("/api/webhooks/")
                and not parsed.username
                and not parsed.password
            )
        except Exception:
            valid = False

        if not valid:
            return jsonify({"success": False, "message": "Invalid webhook URL format."}), 400

        try:
            payload = {
                "embeds": [{
                    "title": "CTFd Notifier — Test",
                    "description": "This is a test notification from CTFRiced.",
                    "color": 3447003,
                }]
            }
            r = rq.post(webhookurl, json=payload, timeout=5)
            if r.status_code in (200, 204):
                return jsonify({"success": True, "message": "Test notification sent."})
            return jsonify({"success": False, "message": f"Discord returned HTTP {r.status_code}."}), 400
        except rq.exceptions.RequestException as e:
            return jsonify({"success": False, "message": "Request failed: " + str(e)}), 500

    return notifier_bp


def test_config(config):
    errors = list()
    if "discord_notifier" in config:
        if config["discord_notifier"]:
            webhookurl = config["discord_webhook_url"]

            try:
                parsed = urlparse(webhookurl)
                valid_hosts = ("discord.com", "discordapp.com")
                valid = (
                    parsed.scheme == "https"
                    and parsed.hostname in valid_hosts
                    and parsed.path.startswith("/api/webhooks/")
                    and not parsed.username  # block user@host bypass
                    and not parsed.password
                )
            except Exception:
                valid = False

            if not valid:
                errors.append("Invalid Webhook URL!")
            else:
                try:
                    r = rq.get(webhookurl, timeout=5)
                    if r.status_code != 200:
                        errors.append("Could not verify that the Webhook is working!")
                except rq.exceptions.RequestException:
                    errors.append("Invalid Webhook URL!")

    if "twitter_notifier" in config:
        if config["twitter_notifier"]:
            try:
                AUTH = tweepy.OAuthHandler(
                    config.get("twitter_consumer_key"),
                    config.get("twitter_consumer_secret"),
                )
                AUTH.set_access_token(
                    config.get("twitter_access_token"),
                    config.get("twitter_access_token_secret"),
                )
                API = tweepy.API(AUTH)
                API.home_timeline()
            except tweepy.TweepError:
                errors.append("Invalid authentication Data!")

    return errors
