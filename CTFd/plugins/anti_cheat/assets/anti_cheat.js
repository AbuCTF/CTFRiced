// Anti-Cheat Plugin JavaScript

class AntiCheatDashboard {
    constructor() {
        this.initializeEventListeners();
        this.initializeTooltips();
        this.startPeriodicUpdates();
    }

    initializeEventListeners() {
        // Auto-scan toggle
        document.addEventListener('change', (e) => {
            if (e.target.id === 'auto-scan-toggle') {
                this.toggleAutoScan(e.target.checked);
            }
        });

        // Real-time updates toggle
        document.addEventListener('change', (e) => {
            if (e.target.id === 'real-time-toggle') {
                this.toggleRealTimeUpdates(e.target.checked);
            }
        });

        // Severity filter
        document.addEventListener('change', (e) => {
            if (e.target.classList.contains('severity-filter')) {
                this.filterAlertsBySeverity();
            }
        });
    }

    initializeTooltips() {
        // Initialize bootstrap tooltips if available
        if (typeof bootstrap !== 'undefined') {
            const tooltipTriggerList = [].slice.call(document.querySelectorAll('[data-bs-toggle="tooltip"]'));
            tooltipTriggerList.map(function (tooltipTriggerEl) {
                return new bootstrap.Tooltip(tooltipTriggerEl);
            });
        }
    }

    async runScan() {
        console.log('runScan() method called');
        const button = document.querySelector('button[onclick="runScan()"]');
        if (!button) {
            console.error('Scan button not found!');
            this.showToast('error', 'Error', 'Scan button not found');
            return;
        }
        
        const originalText = button.innerHTML;
        
        try {
            console.log('Starting scan...');
            button.innerHTML = '<i class="fas fa-spinner fa-spin me-2"></i>Scanning...';
            button.disabled = true;
            button.classList.add('scanning');
            
            const response = await fetch('/admin/anti_cheat/check');
            const data = await response.json();
            
            console.log('Scan response:', data);
            
            if (data.success) {
                this.showToast('success', 'Scan Complete', data.message);
                await this.refreshDashboard();
            } else {
                this.showToast('error', 'Scan Failed', 'An error occurred during the scan');
            }
        } catch (error) {
            this.showToast('error', 'Scan Failed', 'Network error: ' + error.message);
        } finally {
            button.innerHTML = originalText;
            button.disabled = false;
            button.classList.remove('scanning');
        }
    }

    async resolveAlert(alertId, status = 'resolved') {
        try {
            const response = await fetch(`/admin/anti_cheat/alert/${alertId}/resolve`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/x-www-form-urlencoded',
                },
                body: `status=${status}`
            });
            
            const data = await response.json();
            
            if (data.success) {
                this.showToast('success', 'Alert Updated', `Alert has been marked as ${status}`);
                await this.refreshDashboard();
            } else {
                this.showToast('error', 'Update Failed', 'Failed to update alert status');
            }
        } catch (error) {
            this.showToast('error', 'Update Failed', 'Network error: ' + error.message);
        }
    }

    async cleanupDuplicates() {
        const button = document.querySelector('button[onclick="cleanupDuplicates()"]');
        if (!button) {
            this.showToast('error', 'Error', 'Cleanup button not found');
            return;
        }
        
        if (!confirm('Are you sure you want to remove duplicate alerts? This action cannot be undone.')) {
            return;
        }
        
        const originalText = button.innerHTML;
        
        try {
            button.innerHTML = '<i class="fas fa-spinner fa-spin me-2"></i>Cleaning...';
            button.disabled = true;
            
            const response = await fetch('/admin/anti_cheat/cleanup-duplicates', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/x-www-form-urlencoded',
                },
                body: `nonce=${window.nonce || ''}`
            });
            const data = await response.json();
            
            if (data.success) {
                this.showToast('success', 'Cleanup Complete', data.message);
                await this.refreshDashboard();
            } else {
                this.showToast('error', 'Cleanup Failed', data.error || 'An error occurred during cleanup');
            }
        } catch (error) {
            this.showToast('error', 'Cleanup Failed', 'Network error: ' + error.message);
        } finally {
            button.innerHTML = originalText;
            button.disabled = false;
        }
    }

    async refreshDashboard() {
        try {
            // Refresh the page to get latest data
            // In a real implementation, you might want to use AJAX to update specific sections
            setTimeout(() => window.location.reload(), 1000);
        } catch (error) {
            console.error('Failed to refresh dashboard:', error);
        }
    }

    filterAlertsBySeverity() {
        const filters = document.querySelectorAll('.severity-filter:checked');
        const selectedSeverities = Array.from(filters).map(f => f.value);
        const alertCards = document.querySelectorAll('.alert-card');
        
        alertCards.forEach(card => {
            const severity = card.classList.toString().match(/severity-(\w+)/);
            if (severity && selectedSeverities.length > 0) {
                if (selectedSeverities.includes(severity[1])) {
                    card.style.display = 'block';
                } else {
                    card.style.display = 'none';
                }
            } else {
                card.style.display = 'block';
            }
        });
    }

    toggleAutoScan(enabled) {
        if (enabled) {
            this.autoScanInterval = setInterval(() => {
                this.runScan();
            }, 300000); // Run every 5 minutes
            
            this.showToast('info', 'Auto-Scan Enabled', 'Automatic scans will run every 5 minutes');
        } else {
            if (this.autoScanInterval) {
                clearInterval(this.autoScanInterval);
                this.autoScanInterval = null;
            }
            this.showToast('info', 'Auto-Scan Disabled', 'Automatic scans have been disabled');
        }
    }

    toggleRealTimeUpdates(enabled) {
        if (enabled) {
            this.realTimeInterval = setInterval(() => {
                this.checkForNewAlerts();
            }, 30000); // Check every 30 seconds
            
            this.showToast('info', 'Real-Time Updates Enabled', 'Dashboard will update automatically');
        } else {
            if (this.realTimeInterval) {
                clearInterval(this.realTimeInterval);
                this.realTimeInterval = null;
            }
            this.showToast('info', 'Real-Time Updates Disabled', 'Automatic updates have been disabled');
        }
    }

    async checkForNewAlerts() {
        try {
            const response = await fetch('/admin/anti_cheat/api/new-alerts');
            const data = await response.json();
            
            if (data.newAlerts > 0) {
                this.showToast('warning', 'New Alerts', `${data.newAlerts} new security alerts detected`);
                this.updateAlertBadge(data.newAlerts);
            }
        } catch (error) {
            console.error('Failed to check for new alerts:', error);
        }
    }

    updateAlertBadge(count) {
        const badge = document.querySelector('.new-alerts-badge');
        if (badge) {
            badge.textContent = count;
            badge.style.display = count > 0 ? 'inline' : 'none';
        }
    }

    startPeriodicUpdates() {
        // Update timestamps every minute
        setInterval(() => {
            this.updateRelativeTimestamps();
        }, 60000);
    }

    updateRelativeTimestamps() {
        const timestamps = document.querySelectorAll('[data-timestamp]');
        timestamps.forEach(element => {
            const timestamp = new Date(element.dataset.timestamp);
            const now = new Date();
            const diff = now - timestamp;
            
            element.textContent = this.formatRelativeTime(diff);
        });
    }

    formatRelativeTime(diffMs) {
        const minutes = Math.floor(diffMs / 60000);
        const hours = Math.floor(minutes / 60);
        const days = Math.floor(hours / 24);
        
        if (days > 0) return `${days}d ago`;
        if (hours > 0) return `${hours}h ago`;
        if (minutes > 0) return `${minutes}m ago`;
        return 'Just now';
    }

    showToast(type, title, message) {
        console.log('showToast called:', type, title, message);
        const toastId = 'toast-' + Date.now();
        const isSuccess = type === 'success';
        const isWarning = type === 'warning';
        const isInfo = type === 'info';
        
        let bgColor = '#e74c3c'; // error
        let icon = 'fas fa-exclamation-triangle';
        
        if (isSuccess) {
            bgColor = '#27ae60';
            icon = 'fas fa-check-circle';
        } else if (isWarning) {
            bgColor = '#f39c12';
            icon = 'fas fa-exclamation-triangle';
        } else if (isInfo) {
            bgColor = '#3498db';
            icon = 'fas fa-info-circle';
        }
        
        const toast = `
            <div id="${toastId}" style="
                position: fixed;
                top: 20px;
                right: 20px;
                z-index: 9999;
                min-width: 300px;
                max-width: 400px;
                background: ${bgColor};
                color: white;
                border-radius: 8px;
                box-shadow: 0 4px 16px rgba(0,0,0,0.2);
                padding: 16px 20px;
                opacity: 0;
                transform: translateX(100%);
                transition: all 0.3s ease;
            ">
                <div style="display: flex; align-items: flex-start; gap: 12px;">
                    <div style="margin-top: 2px;">
                        <i class="${icon}" style="font-size: 18px;"></i>
                    </div>
                    <div style="flex: 1;">
                        <div style="font-weight: 600; margin-bottom: 4px;">${title}</div>
                        <div style="font-size: 13px; opacity: 0.9;">${message}</div>
                    </div>
                    <button onclick="document.getElementById('${toastId}').remove()" style="
                        background: none; border: none; color: white; font-size: 18px;
                        cursor: pointer; padding: 0; opacity: 0.7;
                    ">×</button>
                </div>
            </div>
        `;
        
        document.body.insertAdjacentHTML('beforeend', toast);
        
        const toastElement = document.getElementById(toastId);
        setTimeout(() => {
            toastElement.style.opacity = '1';
            toastElement.style.transform = 'translateX(0)';
        }, 10);
        
        setTimeout(() => {
            if (toastElement) {
                toastElement.style.opacity = '0';
                toastElement.style.transform = 'translateX(100%)';
                setTimeout(() => toastElement.remove(), 300);
            }
        }, 5000);
    }

    async manualCleanup() {
        const button = document.querySelector('button[onclick="manualCleanup()"]');
        if (!button) {
            this.showToast('error', 'Error', 'Cleanup button not found');
            return;
        }
        
        if (!confirm('Are you sure you want to remove duplicate alerts? This action cannot be undone.')) {
            return;
        }
        
        const originalText = button.innerHTML;
        
        try {
            button.innerHTML = '<i class="fas fa-spinner fa-spin me-2"></i>Cleaning...';
            button.disabled = true;
            
            const response = await fetch('/admin/anti_cheat/manual-cleanup', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/x-www-form-urlencoded',
                },
                body: `nonce=${window.nonce || Session?.nonce || ''}`
            });
            
            if (!response.ok) {
                throw new Error('HTTP ' + response.status);
            }
            
            const data = await response.json();
            
            if (data.success) {
                this.showToast('success', 'Cleanup Complete', data.message);
                await this.refreshDashboard();
            } else {
                this.showToast('error', 'Cleanup Failed', data.error || 'An error occurred during cleanup');
            }
        } catch (error) {
            this.showToast('error', 'Cleanup Failed', 'Network error: ' + error.message);
        } finally {
            button.innerHTML = originalText;
            button.disabled = false;
        }
    }
}

// Initialize dashboard when DOM is loaded
document.addEventListener('DOMContentLoaded', function() {
    console.log('Anti-cheat JavaScript loaded!');
    window.antiCheatDashboard = new AntiCheatDashboard();
    console.log('AntiCheatDashboard initialized:', window.antiCheatDashboard);
});

// Global functions for template usage
function runScan() {
    console.log('runScan() called');
    if (window.antiCheatDashboard) {
        console.log('Calling dashboard runScan method');
        window.antiCheatDashboard.runScan();
    } else {
        console.error('window.antiCheatDashboard not found!');
        alert('Dashboard not initialized. Check console for errors.');
    }
}

function manualCleanup() {
    console.log('manualCleanup() called');
    if (window.antiCheatDashboard) {
        window.antiCheatDashboard.manualCleanup();
    } else {
        console.error('window.antiCheatDashboard not found!');
        alert('Dashboard not initialized. Check console for errors.');
    }
}

function resolveAlert(alertId, status = 'resolved') {
    if (window.antiCheatDashboard) {
        window.antiCheatDashboard.resolveAlert(alertId, status);
    }
}

// Configuration page helpers
function updateRangeValue(input, valueId, isPercentage = false) {
    const valueElement = document.getElementById(valueId);
    if (valueElement) {
        if (isPercentage) {
            valueElement.textContent = Math.round(input.value * 100) + '%';
        } else {
            valueElement.textContent = input.value;
        }
    }
}

// Export for module systems
if (typeof module !== 'undefined' && module.exports) {
    module.exports = AntiCheatDashboard;
}
