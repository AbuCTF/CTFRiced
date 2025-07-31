#!/usr/bin/env python3
"""
Manual Database Migration Script for Docker Challenges Plugin
Run this script if the automatic migration fails
"""

import sys
import os

# Add CTFd to the path
sys.path.insert(0, '/opt/CTFd')

from CTFd import create_app
from CTFd.utils import get_config
from CTFd.models import db
from sqlalchemy import text

def run_migration():
    """Run the database migration manually"""
    app = create_app()
    
    with app.app_context():
        print("Starting manual database migration for Docker Challenges Plugin...")
        
        try:
            # Check current database state
            print("Checking current database schema...")
            
            # Add missing columns to docker_config table
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
                    # Test if column exists
                    result = db.session.execute(text(f"SELECT {column_name} FROM docker_config LIMIT 1")).fetchone()
                    print(f"✓ Column {column_name} already exists")
                except Exception as e:
                    if "Unknown column" in str(e) or "no such column" in str(e):
                        print(f"Adding missing column: {column_name}")
                        try:
                            db.session.execute(text(f"ALTER TABLE docker_config ADD COLUMN {column_name} {column_def}"))
                            db.session.commit()
                            print(f"✓ Successfully added column: {column_name}")
                        except Exception as alter_error:
                            print(f"✗ Error adding column {column_name}: {str(alter_error)}")
                            db.session.rollback()
                    else:
                        print(f"✗ Error checking column {column_name}: {str(e)}")
            
            # Add missing columns to docker_challenge_tracker table
            try:
                result = db.session.execute(text("SELECT docker_config_id FROM docker_challenge_tracker LIMIT 1")).fetchone()
                print("✓ docker_challenge_tracker.docker_config_id already exists")
            except Exception as e:
                if "Unknown column" in str(e) or "no such column" in str(e):
                    print("Adding docker_config_id to docker_challenge_tracker...")
                    try:
                        db.session.execute(text("ALTER TABLE docker_challenge_tracker ADD COLUMN docker_config_id INTEGER"))
                        db.session.commit()
                        print("✓ Successfully added docker_config_id to tracker table")
                    except Exception as alter_error:
                        print(f"✗ Error adding docker_config_id to tracker: {str(alter_error)}")
                        db.session.rollback()
            
            # Add missing columns to docker_challenge table
            try:
                result = db.session.execute(text("SELECT docker_config_id FROM docker_challenge LIMIT 1")).fetchone()
                print("✓ docker_challenge.docker_config_id already exists")
            except Exception as e:
                if "Unknown column" in str(e) or "no such column" in str(e):
                    print("Adding docker_config_id to docker_challenge...")
                    try:
                        db.session.execute(text("ALTER TABLE docker_challenge ADD COLUMN docker_config_id INTEGER"))
                        db.session.commit()
                        print("✓ Successfully added docker_config_id to challenge table")
                    except Exception as alter_error:
                        print(f"✗ Error adding docker_config_id to challenge: {str(alter_error)}")
                        db.session.rollback()
            
            # Migrate existing data
            print("\nMigrating existing data...")
            
            # Update configs without names
            old_configs = db.session.execute(text("SELECT id FROM docker_config WHERE name IS NULL OR name = ''")).fetchall()
            if old_configs:
                print(f"Found {len(old_configs)} configs to migrate...")
                for config_row in old_configs:
                    config_id = config_row[0]
                    db.session.execute(text("""
                        UPDATE docker_config 
                        SET name = CONCAT('Server-', id),
                            is_active = TRUE,
                            status = 'unknown',
                            created_at = NOW()
                        WHERE id = :config_id
                    """), {"config_id": config_id})
                db.session.commit()
                print("✓ Migrated existing configurations")
            else:
                print("✓ No configurations need migration")
            
            # Migrate challenges without server assignment
            challenges_without_server = db.session.execute(text(
                "SELECT id FROM docker_challenge WHERE docker_config_id IS NULL"
            )).fetchall()
            
            if challenges_without_server:
                first_server = db.session.execute(text("SELECT id FROM docker_config ORDER BY id LIMIT 1")).fetchone()
                if first_server:
                    server_id = first_server[0]
                    print(f"Assigning {len(challenges_without_server)} challenges to server {server_id}...")
                    for challenge_row in challenges_without_server:
                        challenge_id = challenge_row[0]
                        db.session.execute(text("""
                            UPDATE docker_challenge 
                            SET docker_config_id = :server_id 
                            WHERE id = :challenge_id
                        """), {"server_id": server_id, "challenge_id": challenge_id})
                    db.session.commit()
                    print("✓ Migrated existing challenges")
            else:
                print("✓ No challenges need migration")
            
            # Migrate container tracker entries
            containers_without_server = db.session.execute(text(
                "SELECT id FROM docker_challenge_tracker WHERE docker_config_id IS NULL"
            )).fetchall()
            
            if containers_without_server:
                first_server = db.session.execute(text("SELECT id FROM docker_config ORDER BY id LIMIT 1")).fetchone()
                if first_server:
                    server_id = first_server[0]
                    print(f"Assigning {len(containers_without_server)} tracker entries to server {server_id}...")
                    for container_row in containers_without_server:
                        container_id = container_row[0]
                        db.session.execute(text("""
                            UPDATE docker_challenge_tracker 
                            SET docker_config_id = :server_id 
                            WHERE id = :container_id
                        """), {"server_id": server_id, "container_id": container_id})
                    db.session.commit()
                    print("✓ Migrated existing container tracker entries")
            else:
                print("✓ No tracker entries need migration")
            
            print("\n🎉 Migration completed successfully!")
            print("You can now restart CTFd and the Docker Challenges plugin should work correctly.")
            
        except Exception as e:
            print(f"\n❌ Migration failed: {str(e)}")
            db.session.rollback()
            return False
        
        return True

if __name__ == "__main__":
    success = run_migration()
    sys.exit(0 if success else 1)
