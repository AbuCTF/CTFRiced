<script>
(function() {
    'use strict';
    
    const canvas = document.createElement('canvas');
    canvas.style.cssText = `
        position: fixed;
        top: 0;
        left: 0;
        width: 100%;
        height: 100%;
        z-index: -3;
        pointer-events: none;
        opacity: 0.6;
    `;
    document.body.appendChild(canvas);

    const ctx = canvas.getContext('2d');
    let particles = [];
    let animationId;
    let isVisible = true;
 
    let lastTime = 0;
    const targetFPS = 60;
    const frameInterval = 1000 / targetFPS;

    function resizeCanvas() {
        canvas.width = window.innerWidth;
        canvas.height = window.innerHeight;
    }

    function createParticle() {
        return {
            x: Math.random() * canvas.width,
            y: Math.random() * canvas.height,
            vx: (Math.random() - 0.5) * 0.3,
            vy: (Math.random() - 0.5) * 0.3,
            size: Math.random() * 2 + 1,
            opacity: Math.random() * 0.6 + 0.2,
            life: Math.random() * 0.5 + 0.5,
            maxLife: Math.random() * 0.5 + 0.5,
            hue: Math.random() * 60 + 180
        };
    }

    function initParticles() {
        const particleCount = Math.min(40, Math.floor((canvas.width * canvas.height) / 25000));
        particles = [];
        for (let i = 0; i < particleCount; i++) {
            particles.push(createParticle());
        }
    }

    function updateParticles() {
        particles.forEach(particle => {
            particle.x += particle.vx;
            particle.y += particle.vy;
            particle.life -= 0.002;

            // Wrap around screen
            if (particle.x < -10) particle.x = canvas.width + 10;
            if (particle.x > canvas.width + 10) particle.x = -10;
            if (particle.y < -10) particle.y = canvas.height + 10;
            if (particle.y > canvas.height + 10) particle.y = -10;

            // Respawn if life ends
            if (particle.life <= 0) {
                Object.assign(particle, createParticle());
            }
        });
    }

    function drawParticles() {
        const isDark = !document.documentElement.hasAttribute('data-bs-theme') || 
                      document.documentElement.getAttribute('data-bs-theme') === 'dark';
        
        ctx.clearRect(0, 0, canvas.width, canvas.height);

        particles.forEach(particle => {
            const alpha = (particle.opacity * particle.life) / particle.maxLife;
            ctx.beginPath();
            ctx.arc(particle.x, particle.y, particle.size, 0, Math.PI * 2);
            
            // Create gradient for each particle
            const gradient = ctx.createRadialGradient(
                particle.x, particle.y, 0,
                particle.x, particle.y, particle.size * 2
            );
            
            if (isDark) {
                gradient.addColorStop(0, `hsla(${particle.hue}, 100%, 80%, ${alpha})`);
                gradient.addColorStop(1, `hsla(${particle.hue}, 100%, 60%, 0)`);
            } else {
                gradient.addColorStop(0, `hsla(${particle.hue - 20}, 70%, 50%, ${alpha})`);
                gradient.addColorStop(1, `hsla(${particle.hue - 20}, 70%, 40%, 0)`);
            }
            
            ctx.fillStyle = gradient;
            ctx.fill();
        });

        particles.forEach((particle, i) => {
            particles.slice(i + 1).forEach(otherParticle => {
                const dx = particle.x - otherParticle.x;
                const dy = particle.y - otherParticle.y;
                const distance = Math.sqrt(dx * dx + dy * dy);

                if (distance < 100) {
                    const opacity = (100 - distance) / 100 * 0.1;
                    const avgLife = (particle.life + otherParticle.life) / 2;
                    
                    ctx.beginPath();
                    ctx.moveTo(particle.x, particle.y);
                    ctx.lineTo(otherParticle.x, otherParticle.y);
                    
                    if (isDark) {
                        ctx.strokeStyle = `rgba(100, 255, 218, ${opacity * avgLife})`;
                    } else {
                        ctx.strokeStyle = `rgba(14, 165, 233, ${opacity * avgLife})`;
                    }
                    
                    ctx.lineWidth = 0.5;
                    ctx.stroke();
                }
            });
        });
    }

    function animate(currentTime) {
        if (!isVisible) {
            animationId = requestAnimationFrame(animate);
            return;
        }

        if (currentTime - lastTime >= frameInterval) {
            updateParticles();
            drawParticles();
            lastTime = currentTime;
        }
        
        animationId = requestAnimationFrame(animate);
    }

    function init() {
        resizeCanvas();
        initParticles();
        animate(0);
    }

    function enhanceUI() {
        document.querySelectorAll('a, button, .btn, .nav-link').forEach(el => {
            if (!el.classList.contains('enhanced')) {
                el.classList.add('enhanced');
                
                el.addEventListener('mouseenter', function() {
                    this.style.transition = 'all 0.3s cubic-bezier(0.4, 0, 0.2, 1)';
                });
            }
        });

        // Enhanced form interactions
        document.querySelectorAll('input, textarea, select').forEach(input => {
            if (!input.classList.contains('enhanced')) {
                input.classList.add('enhanced');
                
                input.addEventListener('focus', function() {
                    this.parentElement?.classList.add('focused');
                });
                
                input.addEventListener('blur', function() {
                    this.parentElement?.classList.remove('focused');
                });
            }
        });

        // Add loading states to forms
        document.querySelectorAll('form').forEach(form => {
            if (!form.classList.contains('enhanced')) {
                form.classList.add('enhanced');
                
                form.addEventListener('submit', function() {
                    const submitBtn = this.querySelector('button[type="submit"]');
                    if (submitBtn && !submitBtn.disabled) {
                        submitBtn.classList.add('loading');
                        submitBtn.disabled = true;
                        
                        // Re-enable after 5 seconds as fallback
                        setTimeout(() => {
                            submitBtn.classList.remove('loading');
                            submitBtn.disabled = false;
                        }, 5000);
                    }
                });
            }
        });
    }

    // Intersection Observer for smooth animations
    function setupAnimations() {
        const observer = new IntersectionObserver((entries) => {
            entries.forEach(entry => {
                if (entry.isIntersecting) {
                    entry.target.style.opacity = '1';
                    entry.target.style.transform = 'translateY(0)';
                }
            });
        }, { 
            threshold: 0.1,
            rootMargin: '0px 0px -50px 0px'
        });

        // Observe elements for animation
        document.querySelectorAll('.card, .alert, .jumbotron, .table').forEach(el => {
            if (!el.classList.contains('animated')) {
                el.classList.add('animated');
                el.style.opacity = '0';
                el.style.transform = 'translateY(20px)';
                el.style.transition = 'opacity 0.6s ease, transform 0.6s ease';
                observer.observe(el);
            }
        });
    }

    function enhanceScrolling() {
        document.querySelectorAll('a[href^="#"]').forEach(anchor => {
            anchor.addEventListener('click', function(e) {
                const href = this.getAttribute('href');
                if (href === '#') return;
                
                const target = document.querySelector(href);
                if (target) {
                    e.preventDefault();
                    target.scrollIntoView({
                        behavior: 'smooth',
                        block: 'start'
                    });
                }
            });
        });
    }

    // Theme transition effects
    function handleThemeTransition() {
        const observer = new MutationObserver((mutations) => {
            mutations.forEach((mutation) => {
                if (mutation.type === 'attributes' && mutation.attributeName === 'data-bs-theme') {
                    document.body.style.transition = 'all 0.4s ease';
                    setTimeout(() => {
                        document.body.style.transition = '';
                    }, 400);
                }
            });
        });

        observer.observe(document.documentElement, {
            attributes: true,
            attributeFilter: ['data-bs-theme']
        });
    }

    // Keyboard navigation enhancement
    function enhanceKeyboardNav() {
        document.addEventListener('keydown', function(e) {
            if (e.key === 'Tab') {
                document.body.classList.add('keyboard-navigation');
            }
        });

        document.addEventListener('mousedown', function() {
            document.body.classList.remove('keyboard-navigation');
        });
    }

    // Initialize everything when DOM is ready
    function initializeTheme() {
        init();
        enhanceUI();
        setupAnimations();
        enhanceScrolling();
        handleThemeTransition();
        enhanceKeyboardNav();
        
        // Re-enhance UI elements when new content is added
        const contentObserver = new MutationObserver(() => {
            enhanceUI();
            setupAnimations();
        });
        
        contentObserver.observe(document.body, {
            childList: true,
            subtree: true
        });
    }

    // Event listeners
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', initializeTheme);
    } else {
        initializeTheme();
    }

    window.addEventListener('resize', () => {
        resizeCanvas();
        initParticles();
    });

    // Performance optimization - pause animation when tab is not visible
    document.addEventListener('visibilitychange', () => {
        isVisible = !document.hidden;
    });

    // Cleanup
    window.addEventListener('beforeunload', () => {
        cancelAnimationFrame(animationId);
    });

    // Add CSS for keyboard navigation
    const style = document.createElement('style');
    style.textContent = `
        .keyboard-navigation *:focus {
            outline: 2px solid var(--accent-color) !important;
            outline-offset: 2px !important;
        }
        
        .focused {
            transform: scale(1.02);
        }
        
        .enhanced {
            transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
        }
    `;
    document.head.appendChild(style);
})();
</script>