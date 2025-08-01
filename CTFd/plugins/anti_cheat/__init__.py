import os
import hashlib
from datetime import datetime, timedelta
from collections import defaultdict
from difflib import SequenceMatcher

from flask import Blueprint, render_template, request, jsonify, url_for, redirect
from CTFd.models import db, Teams, Users, Submissions, Solves, Challenges
from CTFd.utils.decorators import admins_only
from CTFd.plugins import register_plugin_assets_directory
from sqlalchemy import and_, or_, func, desc

# Plugin metadata
__version__ = "1.0.0"

class AntiCheatConfig(db.Model):
    __tablename__ = 'anti_cheat_config'
    
    id = db.Column(db.Integer, primary_key=True)
    duplicate_flag_threshold = db.Column(db.Integer, default=1)  # Min participants for duplicate flag alert
    brute_force_threshold = db.Column(db.Integer, default=10)    # Max attempts per minute
    brute_force_window = db.Column(db.Integer, default=60)       # Time window in seconds
    ip_sharing_threshold = db.Column(db.Integer, default=3)      # Max teams per IP
    sequence_similarity_threshold = db.Column(db.Float, default=0.8)  # LCS similarity threshold
    time_delta_threshold = db.Column(db.Integer, default=30)     # Max time difference in seconds
    auto_ban_enabled = db.Column(db.Boolean, default=False)     # Auto-ban suspicious teams
    notification_enabled = db.Column(db.Boolean, default=True)  # Send notifications
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class AntiCheatAlert(db.Model):
    __tablename__ = 'anti_cheat_alerts'
    
    id = db.Column(db.Integer, primary_key=True)
    alert_type = db.Column(db.String(50), nullable=False)  # duplicate_flag, brute_force, ip_sharing, sequence_match
    severity = db.Column(db.String(20), default='medium')  # low, medium, high, critical
    team_id = db.Column(db.Integer, db.ForeignKey('teams.id'), nullable=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    challenge_id = db.Column(db.Integer, db.ForeignKey('challenges.id'), nullable=True)
    description = db.Column(db.Text, nullable=False)
    evidence = db.Column(db.JSON)  # Store detection evidence as JSON
    ip_address = db.Column(db.String(45))  # IPv4/IPv6
    status = db.Column(db.String(20), default='open')  # open, investigating, resolved, false_positive
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    resolved_at = db.Column(db.DateTime, nullable=True)
    resolved_by = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    
    # Relationships
    team = db.relationship('Teams', foreign_keys=[team_id])
    user = db.relationship('Users', foreign_keys=[user_id])
    challenge = db.relationship('Challenges')
    resolver = db.relationship('Users', foreign_keys=[resolved_by])

class AntiCheatDetector:
    """Core anti-cheat detection algorithms"""
    
    def __init__(self):
        self.config = self._get_config()
    
    def _get_config(self):
        """Get or create anti-cheat configuration"""
        config = AntiCheatConfig.query.first()
        if not config:
            config = AntiCheatConfig()
            db.session.add(config)
            db.session.commit()
        return config
    
    def _alert_exists(self, alert_type, evidence_hash):
        """Check if an alert with the same type and evidence already exists"""
        try:
            existing = db.session.query(AntiCheatAlert).filter(
                AntiCheatAlert.alert_type == alert_type,
                AntiCheatAlert.evidence.contains(f'"evidence_hash": "{evidence_hash}"')
            ).first()
            return existing is not None
        except Exception:
            # Fall back to checking by description and challenge for duplicate flags
            if alert_type == 'duplicate_flag':
                return self._check_duplicate_flag_exists(evidence_hash)
            return False
    
    def _check_duplicate_flag_exists(self, evidence_hash):
        """Fallback method to check for duplicate flag alerts"""
        try:
            existing_alerts = db.session.query(AntiCheatAlert).filter(
                AntiCheatAlert.alert_type == 'duplicate_flag',
                AntiCheatAlert.status == 'open'
            ).all()
            
            for alert in existing_alerts:
                if alert.evidence and alert.evidence.get('evidence_hash') == evidence_hash:
                    return True
            return False
        except Exception:
            return False
    
    def detect_duplicate_flags(self):
        """Detect teams/users submitting identical flags on dynamic challenges"""
        alerts = []
        
        # Get dynamic challenges (including docker challenges which are dynamic)
        dynamic_challenges = db.session.query(Challenges).filter(
            or_(Challenges.type == 'dynamic', Challenges.type == 'docker')
        ).all()
        
        # Production: Use app.logger.info() for scanning progress
        # print(f"Found {len(dynamic_challenges)} dynamic/docker challenges")
        
        for challenge in dynamic_challenges:
            # Group submissions by flag content
            submissions = db.session.query(Submissions).filter(
                Submissions.challenge_id == challenge.id,
                Submissions.type == 'correct'  # Only check correct submissions
            ).all()
            
            # Production: Use app.logger.debug() instead
            # print(f"Challenge '{challenge.name}': Found {len(submissions)} correct submissions")
            
            # Also check all submissions (not just correct ones) for debugging
            all_submissions = db.session.query(Submissions).filter(
                Submissions.challenge_id == challenge.id
            ).all()
            # Production: Use app.logger.debug() instead
            # print(f"Challenge '{challenge.name}': Found {len(all_submissions)} total submissions")
            
            # Debug loop for production logging
            for sub in all_submissions:
                # Production: Use app.logger.debug() instead
                # print(f"  Submission: team_id={sub.team_id}, type={sub.type}, provided='{sub.provided[:30]}...', date={sub.date}")
                pass  # Keep for production logging hook
            
            flag_groups = defaultdict(list)
            for submission in submissions:
                flag_groups[submission.provided].append(submission)
            
            # Production: Use app.logger.debug() instead
            # print(f"Challenge '{challenge.name}': Found {len(flag_groups)} unique flags")
            
            # Check for suspicious patterns
            for flag_content, subs in flag_groups.items():
                # Get unique participants (teams or users)
                participants = set()
                for sub in subs:
                    if sub.team_id:
                        participants.add(('team', sub.team_id))
                    elif sub.user_id:
                        participants.add(('user', sub.user_id))
                
                # Production: Use app.logger.debug() instead
                # print(f"  Flag '{flag_content[:20]}...': {len(participants)} participants (threshold: {self.config.duplicate_flag_threshold})")
                
                if len(participants) >= self.config.duplicate_flag_threshold:
                    # Create a unique hash for this specific duplicate flag detection
                    evidence_content = f"{challenge.id}_{flag_content}_{sorted(participants)}"
                    evidence_hash = hashlib.sha256(evidence_content.encode()).hexdigest()[:16]
                    
                    # Check if this exact alert already exists
                    if self._alert_exists('duplicate_flag', evidence_hash):
                        # Production: Use app.logger.debug() instead
                        # print(f"    Alert already exists for this duplicate flag, skipping...")
                        continue
                    
                    # Get participant names for evidence
                    participant_names = []
                    team_ids = []
                    user_ids = []
                    
                    for p_type, p_id in participants:
                        if p_type == 'team':
                            team = db.session.query(Teams).filter_by(id=p_id).first()
                            if team:
                                participant_names.append(f"Team: {team.name}")
                                team_ids.append(p_id)
                        elif p_type == 'user':
                            user = db.session.query(Users).filter_by(id=p_id).first()
                            if user:
                                participant_names.append(f"User: {user.name}")
                                user_ids.append(p_id)
                    
                    evidence = {
                        'evidence_hash': evidence_hash,  # Add unique hash to evidence
                        'flag_content_hash': hashlib.sha256(flag_content.encode()).hexdigest()[:16],
                        'full_flag_content': flag_content,  # Store full flag for analysis
                        'participant_count': len(participants),
                        'team_ids': team_ids,
                        'user_ids': user_ids,
                        'participant_names': participant_names,
                        'submission_times': [sub.date.isoformat() for sub in subs],
                        'challenge_name': challenge.name,
                        'challenge_type': challenge.type,
                        'flag_preview': flag_content[:20] + '...' if len(flag_content) > 20 else flag_content
                    }
                    
                    alert = AntiCheatAlert(
                        alert_type='duplicate_flag',
                        severity='critical' if len(participants) > 10 else 'high' if len(participants) > 5 else 'medium',
                        challenge_id=challenge.id,
                        description=f"Identical flag '{flag_content[:20]}...' submitted by {len(participants)} participants on {challenge.type} challenge '{challenge.name}'",
                        evidence=evidence
                    )
                    alerts.append(alert)
                    # Production: Use app.logger.info() instead
                    # print(f"    ALERT CREATED: {len(participants)} teams shared the same flag!")
        
        # Production: Use app.logger.info() instead
        # print(f"Duplicate flag detection complete: {len(alerts)} alerts created")
        return alerts
    
    def detect_brute_force(self):
        """Detect flag brute force attempts"""
        alerts = []
        
        # Check recent submissions for brute force patterns
        cutoff_time = datetime.utcnow() - timedelta(seconds=self.config.brute_force_window)
        
        # Group by team and challenge
        recent_submissions = db.session.query(Submissions).filter(
            Submissions.date >= cutoff_time
        ).all()
        
        team_challenge_attempts = defaultdict(list)
        for submission in recent_submissions:
            key = (submission.team_id, submission.challenge_id)
            team_challenge_attempts[key].append(submission)
        
        for (team_id, challenge_id), attempts in team_challenge_attempts.items():
            if len(attempts) > self.config.brute_force_threshold:
                # Create unique hash for this brute force detection
                evidence_content = f"brute_force_{team_id}_{challenge_id}_{len(attempts)}_{self.config.brute_force_window}"
                evidence_hash = hashlib.sha256(evidence_content.encode()).hexdigest()[:16]
                
                # Check if this exact alert already exists
                if self._alert_exists('brute_force', evidence_hash):
                    continue
                
                challenge = db.session.query(Challenges).get(challenge_id)
                team = db.session.query(Teams).get(team_id)
                
                evidence = {
                    'evidence_hash': evidence_hash,
                    'attempt_count': len(attempts),
                    'time_window': self.config.brute_force_window,
                    'first_attempt': attempts[0].date.isoformat(),
                    'last_attempt': attempts[-1].date.isoformat(),
                    'challenge_name': challenge.name if challenge else 'Unknown',
                    'team_name': team.name if team else 'Unknown'
                }
                
                alert = AntiCheatAlert(
                    alert_type='brute_force',
                    severity='high',
                    team_id=team_id,
                    challenge_id=challenge_id,
                    description=f"Brute force detected: {len(attempts)} attempts in {self.config.brute_force_window}s",
                    evidence=evidence
                )
                alerts.append(alert)
        
        return alerts
    
    def detect_ip_sharing(self):
        """Detect account sharing from multiple IPs"""
        alerts = []
        
        # Get recent submissions with IP tracking
        recent_time = datetime.utcnow() - timedelta(hours=24)
        
        # Group submissions by team and IP
        team_ips = defaultdict(set)
        submissions = db.session.query(Submissions).filter(
            Submissions.date >= recent_time
        ).all()
        
        for submission in submissions:
            if submission.team_id and submission.ip:
                team_ips[submission.team_id].add(submission.ip)
        
        # Check for teams with too many IPs
        for team_id, ips in team_ips.items():
            if len(ips) > self.config.ip_sharing_threshold:
                # Create unique hash for this team's IP usage
                evidence_content = f"team_multi_ip_{team_id}_{len(ips)}_{sorted(ips)}"
                evidence_hash = hashlib.sha256(evidence_content.encode()).hexdigest()[:16]
                
                # Check if this exact alert already exists
                if self._alert_exists('ip_sharing', evidence_hash):
                    continue
                
                team = db.session.query(Teams).get(team_id)
                
                evidence = {
                    'evidence_hash': evidence_hash,
                    'ip_count': len(ips),
                    'ip_addresses': list(ips),
                    'team_name': team.name if team else 'Unknown',
                    'detection_window': '24 hours'
                }
                
                alert = AntiCheatAlert(
                    alert_type='ip_sharing',
                    severity='medium',
                    team_id=team_id,
                    description=f"Multiple IPs detected for team: {len(ips)} different addresses",
                    evidence=evidence
                )
                alerts.append(alert)
        
        # Also check for single IP used by multiple teams
        ip_teams = defaultdict(set)
        for submission in submissions:
            if submission.team_id and submission.ip:
                ip_teams[submission.ip].add(submission.team_id)
        
        for ip, teams in ip_teams.items():
            if len(teams) > self.config.ip_sharing_threshold:
                # Create unique hash for this IP's team usage
                evidence_content = f"ip_multi_team_{ip}_{len(teams)}_{sorted(teams)}"
                evidence_hash = hashlib.sha256(evidence_content.encode()).hexdigest()[:16]
                
                # Check if this exact alert already exists
                if self._alert_exists('ip_sharing', evidence_hash):
                    continue
                
                evidence = {
                    'evidence_hash': evidence_hash,
                    'team_count': len(teams),
                    'team_ids': list(teams),
                    'ip_address': ip,
                    'detection_window': '24 hours'
                }
                
                alert = AntiCheatAlert(
                    alert_type='ip_sharing',
                    severity='high',
                    description=f"Single IP used by {len(teams)} different teams",
                    evidence=evidence,
                    ip_address=ip
                )
                alerts.append(alert)
        
        return alerts
    
    def detect_submission_sequence_similarity(self):
        """Detect similar submission sequences using Longest Common Subsequence"""
        alerts = []
        
        # Get all teams' submission sequences
        teams = db.session.query(Teams).all()
        team_sequences = {}
        
        for team in teams:
            # Get team's submission sequence (challenge IDs in order)
            submissions = db.session.query(Submissions).filter(
                Submissions.team_id == team.id
            ).order_by(Submissions.date).all()
            
            sequence = [sub.challenge_id for sub in submissions]
            team_sequences[team.id] = {
                'sequence': sequence,
                'team_name': team.name,
                'timestamps': [sub.date for sub in submissions]
            }
        
        # Compare all team pairs
        team_ids = list(team_sequences.keys())
        for i in range(len(team_ids)):
            for j in range(i + 1, len(team_ids)):
                team1_id, team2_id = team_ids[i], team_ids[j]
                seq1 = team_sequences[team1_id]['sequence']
                seq2 = team_sequences[team2_id]['sequence']
                
                if len(seq1) < 3 or len(seq2) < 3:  # Skip teams with too few submissions
                    continue
                
                # Calculate LCS similarity
                similarity = self._lcs_similarity(seq1, seq2)
                
                if similarity > self.config.sequence_similarity_threshold:
                    # Create unique hash for this sequence match
                    evidence_content = f"sequence_match_{min(team1_id, team2_id)}_{max(team1_id, team2_id)}_{similarity:.3f}"
                    evidence_hash = hashlib.sha256(evidence_content.encode()).hexdigest()[:16]
                    
                    # Check if this exact alert already exists
                    if self._alert_exists('sequence_match', evidence_hash):
                        continue
                    
                    # Check time delta similarity
                    time_similarity = self._time_delta_similarity(
                        team_sequences[team1_id]['timestamps'],
                        team_sequences[team2_id]['timestamps']
                    )
                    
                    evidence = {
                        'evidence_hash': evidence_hash,
                        'team1_id': team1_id,
                        'team2_id': team2_id,
                        'team1_name': team_sequences[team1_id]['team_name'],
                        'team2_name': team_sequences[team2_id]['team_name'],
                        'sequence_similarity': similarity,
                        'time_similarity': time_similarity,
                        'sequence_length1': len(seq1),
                        'sequence_length2': len(seq2)
                    }
                    
                    severity = 'critical' if similarity > 0.9 and time_similarity > 0.8 else 'high'
                    
                    alert = AntiCheatAlert(
                        alert_type='sequence_match',
                        severity=severity,
                        description=f"Highly similar submission sequences detected between teams (similarity: {similarity:.2%})",
                        evidence=evidence
                    )
                    alerts.append(alert)
        
        return alerts
    
    def _lcs_similarity(self, seq1, seq2):
        """Calculate Longest Common Subsequence similarity between two sequences"""
        matcher = SequenceMatcher(None, seq1, seq2)
        return matcher.ratio()
    
    def _time_delta_similarity(self, times1, times2):
        """Calculate similarity of time deltas between submissions"""
        if len(times1) < 2 or len(times2) < 2:
            return 0.0
        
        # Calculate time deltas (differences between consecutive submissions)
        deltas1 = [(times1[i+1] - times1[i]).total_seconds() for i in range(len(times1)-1)]
        deltas2 = [(times2[i+1] - times2[i]).total_seconds() for i in range(len(times2)-1)]
        
        # Normalize deltas to minutes
        deltas1 = [d/60 for d in deltas1]
        deltas2 = [d/60 for d in deltas2]
        
        # Use sequence matcher on normalized deltas
        matcher = SequenceMatcher(None, deltas1, deltas2)
        return matcher.ratio()
    
    def cleanup_existing_duplicates(self):
        """Clean up any existing duplicate alerts before running new detection"""
        try:
            # Production: Use app.logger.info() instead
            # print("Starting automatic duplicate cleanup...")
            
            # Get all duplicate flag alerts grouped by challenge and flag content
            duplicate_alerts = db.session.query(AntiCheatAlert).filter(
                AntiCheatAlert.alert_type == 'duplicate_flag'
            ).order_by(AntiCheatAlert.created_at).all()
            
            # Group by challenge and flag content hash
            groups = defaultdict(list)
            for alert in duplicate_alerts:
                if alert.evidence:
                    key = f"{alert.challenge_id}_{alert.evidence.get('flag_content_hash', '')}"
                    groups[key].append(alert)
            
            removed_count = 0
            for key, alerts_group in groups.items():
                if len(alerts_group) > 1:
                    # Keep the first alert, remove the rest
                    for alert in alerts_group[1:]:
                        # Production: Use app.logger.debug() instead
                        # print(f"    Removing duplicate alert ID {alert.id}: {alert.description[:50]}...")
                        db.session.delete(alert)
                        removed_count += 1
            
            if removed_count > 0:
                db.session.commit()
                # Production: Use app.logger.info() instead
                # print(f"Removed {removed_count} duplicate alerts")
            else:
                # Production: Use app.logger.debug() instead
                # print("No duplicate alerts found to remove")
                pass
                
            return removed_count
            
        except Exception as e:
            # Production: Use app.logger.error() instead
            # print(f"Error during duplicate cleanup: {e}")
            db.session.rollback()
            return 0

    def run_all_detections(self):
        """Run all anti-cheat detection algorithms with automatic duplicate cleanup"""
        # Production: Use app.logger.info() instead
        # print(f"Starting anti-cheat detection with automatic cleanup...")
        
        # First, clean up any existing duplicates
        cleaned_count = self.cleanup_existing_duplicates()
        
        all_alerts = []
        
        try:
            duplicate_alerts = self.detect_duplicate_flags()
            # Production: Use app.logger.info() instead
            # print(f"Duplicate flag detection: found {len(duplicate_alerts)} new alerts")
            all_alerts.extend(duplicate_alerts)
        except Exception as e:
            # Production: Use app.logger.error() instead
            # print(f"Error in duplicate flag detection: {e}")
            import traceback
            traceback.print_exc()
        
        try:
            brute_force_alerts = self.detect_brute_force()
            # Production: Use app.logger.info() instead
            # print(f"Brute force detection: found {len(brute_force_alerts)} alerts")
            all_alerts.extend(brute_force_alerts)
        except Exception as e:
            # Production: Use app.logger.error() instead
            # print(f"Error in brute force detection: {e}")
            pass
        
        try:
            ip_sharing_alerts = self.detect_ip_sharing()
            # Production: Use app.logger.info() instead
            # print(f"IP sharing detection: found {len(ip_sharing_alerts)} alerts")
            all_alerts.extend(ip_sharing_alerts)
        except Exception as e:
            # Production: Use app.logger.error() instead
            # print(f"Error in IP sharing detection: {e}")
            pass
        
        try:
            sequence_alerts = self.detect_submission_sequence_similarity()
            # Production: Use app.logger.info() instead
            # print(f"Sequence similarity detection: found {len(sequence_alerts)} alerts")
            all_alerts.extend(sequence_alerts)
        except Exception as e:
            # Production: Use app.logger.error() instead
            # print(f"Error in sequence similarity detection: {e}")
            pass
        
        # Production: Use app.logger.info() instead
        # print(f"Total new alerts to save: {len(all_alerts)}")
        
        # Save alerts to database
        for i, alert in enumerate(all_alerts):
            # Production: Use app.logger.debug() instead
            # print(f"Saving alert {i+1}: {alert.alert_type} - {alert.description}")
            db.session.add(alert)
        
        if all_alerts:
            try:
                db.session.commit()
                # Production: Use app.logger.info() instead
                # print(f"Successfully committed {len(all_alerts)} new alerts to database")
            except Exception as e:
                # Production: Use app.logger.error() instead
                # print(f"Error committing alerts to database: {e}")
                db.session.rollback()
                import traceback
                traceback.print_exc()
        else:
            # Production: Use app.logger.debug() instead
            # print("No new alerts to save")
            pass
        
        return all_alerts, cleaned_count

def create_anti_cheat_blueprint():
    """Create the anti-cheat admin blueprint"""
    anti_cheat_bp = Blueprint(
        'anti_cheat',
        __name__,
        template_folder='templates',
        static_folder='assets',
        url_prefix='/admin/anti_cheat'
    )
    
    @anti_cheat_bp.route('/')
    @admins_only
    def dashboard():
        """Anti-cheat dashboard"""
        # Get recent alerts
        alerts = db.session.query(AntiCheatAlert).order_by(
            desc(AntiCheatAlert.created_at)
        ).limit(50).all()
        
        # Debug logging
        # Production: Use app.logger.debug() instead
        # print(f"Dashboard: Found {len(alerts)} alerts in database")
        for alert in alerts:
            # Production: Use app.logger.debug() instead
            # print(f"Alert: {alert.alert_type} - {alert.description} - {alert.created_at}")
            pass
        
        # Get alert statistics
        stats = {
            'total_alerts': db.session.query(AntiCheatAlert).count(),
            'open_alerts': db.session.query(AntiCheatAlert).filter(
                AntiCheatAlert.status == 'open'
            ).count(),
            'critical_alerts': db.session.query(AntiCheatAlert).filter(
                AntiCheatAlert.severity == 'critical'
            ).count(),
            'alerts_today': db.session.query(AntiCheatAlert).filter(
                AntiCheatAlert.created_at >= datetime.utcnow().date()
            ).count()
        }
        
        # Production: Use app.logger.debug() instead
        # print(f"Dashboard stats: {stats}")
        
        # Get alert type breakdown
        alert_types = db.session.query(
            AntiCheatAlert.alert_type,
            func.count(AntiCheatAlert.id)
        ).group_by(AntiCheatAlert.alert_type).all()
        
        return render_template(
            'anti_cheat_dashboard.html',
            alerts=alerts,
            stats=stats,
            alert_types=dict(alert_types)
        )
    
    @anti_cheat_bp.route('/check')
    @admins_only
    def run_check():
        """Run anti-cheat detection with automatic duplicate cleanup"""
        detector = AntiCheatDetector()
        alerts, cleaned_count = detector.run_all_detections()
        
        # Debug: Check total alerts in database
        total_alerts = db.session.query(AntiCheatAlert).count()
        
        message_parts = []
        if cleaned_count > 0:
            message_parts.append(f'Cleaned {cleaned_count} duplicate alerts')
        message_parts.append(f'Found {len(alerts)} new alerts')
        message_parts.append(f'Total in database: {total_alerts}')
        
        return jsonify({
            'success': True,
            'alerts_found': len(alerts),
            'duplicates_cleaned': cleaned_count,
            'total_in_db': total_alerts,
            'message': '. '.join(message_parts)
        })
    
    @anti_cheat_bp.route('/test-detection')
    @admins_only
    def test_detection():
        """Test detection logic for debugging"""
        try:
            # Test duplicate flag detection
            dynamic_challenges = db.session.query(Challenges).filter(
                or_(Challenges.type == 'dynamic', Challenges.type == 'docker')
            ).all()
            
            results = []
            for challenge in dynamic_challenges:
                submissions = db.session.query(Submissions).filter(
                    Submissions.challenge_id == challenge.id,
                    Submissions.type == 'correct'
                ).all()
                
                flag_groups = defaultdict(list)
                for submission in submissions:
                    flag_groups[submission.provided].append(submission)
                
                for flag_content, subs in flag_groups.items():
                    participants = set()
                    for sub in subs:
                        if sub.team_id:
                            participants.add(('team', sub.team_id))
                        elif sub.user_id:
                            participants.add(('user', sub.user_id))
                    
                    results.append({
                        'challenge': challenge.name,
                        'challenge_type': challenge.type,
                        'flag_preview': flag_content[:30] + '...',
                        'participant_count': len(participants),
                        'threshold': 2,  # Current threshold
                        'should_alert': len(participants) >= 2
                    })
            
            return jsonify({
                'success': True,
                'results': results,
                'total_challenges': len(dynamic_challenges)
            })
        except Exception as e:
            # Production: Use app.logger.error() instead
            # print(f"Error in test detection: {str(e)}")
            import traceback
            traceback.print_exc()
            return jsonify({
                'success': False,
                'error': str(e)
            }), 500
    
    @anti_cheat_bp.route('/config', methods=['GET', 'POST'])
    @admins_only
    def config():
        """Anti-cheat configuration"""
        config = AntiCheatConfig.query.first()
        if not config:
            config = AntiCheatConfig()
            db.session.add(config)
            db.session.commit()
        
        if request.method == 'POST':
            # Update configuration
            config.duplicate_flag_threshold = int(request.form.get('duplicate_flag_threshold', 1))
            config.brute_force_threshold = int(request.form.get('brute_force_threshold', 10))
            config.brute_force_window = int(request.form.get('brute_force_window', 60))
            config.ip_sharing_threshold = int(request.form.get('ip_sharing_threshold', 3))
            config.sequence_similarity_threshold = float(request.form.get('sequence_similarity_threshold', 0.8))
            config.time_delta_threshold = int(request.form.get('time_delta_threshold', 30))
            config.auto_ban_enabled = 'auto_ban_enabled' in request.form
            config.notification_enabled = 'notification_enabled' in request.form
            config.updated_at = datetime.utcnow()
            
            db.session.commit()
            
            return redirect(url_for('anti_cheat.config'))
        
        return render_template('anti_cheat_config.html', config=config)
    
    @anti_cheat_bp.route('/alert/<int:alert_id>')
    @admins_only
    def view_alert(alert_id):
        """View specific alert details"""
        alert = db.session.query(AntiCheatAlert).get_or_404(alert_id)
        return render_template('anti_cheat_alert.html', alert=alert)
    
    @anti_cheat_bp.route('/alert/<int:alert_id>/resolve', methods=['POST'])
    @admins_only
    def resolve_alert(alert_id):
        """Resolve an alert"""
        try:
            # Try to validate CSRF token if available
            nonce = request.form.get('nonce')
            if nonce:
                try:
                    from CTFd.utils.security.csrf import validate_csrf
                    validate_csrf(nonce)
                except ImportError:
                    # Fallback for older CTFd versions
                    try:
                        from CTFd.utils.crypto import verify_csrf_token
                        verify_csrf_token(nonce)
                    except ImportError:
                        # If CSRF validation not available, proceed anyway for admin
                        # Production: Use app.logger.warning() instead
                        # print("CSRF validation not available, proceeding...")
                        pass
                        pass
            
            alert = db.session.query(AntiCheatAlert).get_or_404(alert_id)
            alert.status = request.form.get('status', 'resolved')
            alert.resolved_at = datetime.utcnow()
            # alert.resolved_by = current_user.id  # Would need proper user context
            
            db.session.commit()
            
            return jsonify({'success': True, 'message': 'Alert has been resolved successfully'})
        except Exception as e:
            # Production: Use app.logger.error() instead  
            # print(f"Error resolving alert: {e}")
            import traceback
            traceback.print_exc()
            return jsonify({'success': False, 'error': str(e)}), 500
    
    @anti_cheat_bp.route('/cleanup-duplicates', methods=['POST'])
    @admins_only
    def cleanup_duplicates():
        """Legacy endpoint - duplicates are now prevented automatically during scanning"""
        try:
            # Try to validate CSRF token if available
            nonce = request.form.get('nonce')
            if nonce:
                try:
                    from CTFd.utils.security.csrf import validate_csrf
                    validate_csrf(nonce)
                except ImportError:
                    # Fallback for older CTFd versions or skip validation
                    pass
            
            return jsonify({
                'success': True,
                'removed_count': 0,
                'message': 'Duplicate prevention is now automatic during scanning. No manual cleanup needed.'
            })
            
        except Exception as e:
            return jsonify({'success': False, 'error': str(e)}), 500

    @anti_cheat_bp.route('/alert/<int:alert_id>/delete', methods=['POST'])
    @admins_only
    def delete_alert(alert_id):
        """Delete a resolved alert permanently"""
        try:
            # Find the alert
            alert = AntiCheatAlert.query.get_or_404(alert_id)
            
            # Only allow deleting resolved alerts
            if alert.status != 'resolved':
                return jsonify({
                    'success': False, 
                    'error': 'Only resolved alerts can be deleted'
                }), 400
            
            # Try to validate CSRF token if available
            nonce = request.form.get('nonce')
            if nonce:
                try:
                    from CTFd.utils.security.csrf import validate_csrf
                    validate_csrf(nonce)
                except ImportError:
                    # Fallback for older CTFd versions or skip validation
                    pass
            
            # Delete the alert
            db.session.delete(alert)
            db.session.commit()
            
            return jsonify({
                'success': True,
                'message': f'Alert #{alert_id} has been permanently deleted'
            })
            
        except Exception as e:
            db.session.rollback()
            # Production: Use app.logger.error() instead
            # print(f"Error deleting alert: {e}")
            import traceback
            traceback.print_exc()
            return jsonify({'success': False, 'error': str(e)}), 500

    @anti_cheat_bp.route('/manual-cleanup', methods=['POST'])
    @admins_only
    def manual_cleanup():
        """Manually clean up duplicate alerts"""
        try:
            # Try to validate CSRF token if available
            nonce = request.form.get('nonce')
            if nonce:
                try:
                    from CTFd.utils.security.csrf import validate_csrf
                    validate_csrf(nonce)
                except ImportError:
                    # Fallback for older CTFd versions or skip validation
                    pass
            
            detector = AntiCheatDetector()
            removed_count = detector.cleanup_existing_duplicates()
            
            return jsonify({
                'success': True,
                'removed_count': removed_count,
                'message': f'Manual cleanup complete. Removed {removed_count} duplicate alerts.'
            })
            
        except Exception as e:
            # Production: Use app.logger.error() instead
            # print(f"Error in manual cleanup: {e}")
            import traceback
            traceback.print_exc()
            return jsonify({'success': False, 'error': str(e)}), 500
    
    return anti_cheat_bp

def load(app):
    """Load the anti-cheat plugin"""
    # Create database tables
    app.db.create_all()
    
    # Register the blueprint
    blueprint = create_anti_cheat_blueprint()
    app.register_blueprint(blueprint)
    
    # Register assets directory - use the actual file system path
    assets_path = os.path.join(os.path.dirname(__file__), "assets")
    register_plugin_assets_directory(
        app, base_path=assets_path
    )
    
    # Production: Plugin loaded successfully (remove in production)
# print("Anti-Cheat plugin loaded successfully")
