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
        opacity: 0.9;
    `;
    document.body.appendChild(canvas);

    const ctx = canvas.getContext('2d');
    let particles = [];
    let geometricShapes = [];
    let wavePath = [];
    let animationId;
    let isVisible = true;
    let time = 0;
 
    let lastTime = 0;
    const targetFPS = 60;
    const frameInterval = 1000 / targetFPS;

    function resizeCanvas() {
        canvas.width = window.innerWidth;
        canvas.height = window.innerHeight;
        initWavePath();
    }

    function createParticle() {
        return {
            x: Math.random() * canvas.width,
            y: Math.random() * canvas.height,
            vx: (Math.random() - 0.5) * 0.3,
            vy: (Math.random() - 0.5) * 0.3,
            size: Math.random() * 2.5 + 1,
            opacity: Math.random() * 0.7 + 0.3,
            life: Math.random() * 0.8 + 0.6,
            maxLife: Math.random() * 0.8 + 0.6,
            hue: Math.random() * 60 + 170, // Wider cyan-purple range
            pulseSpeed: Math.random() * 0.03 + 0.02
        };
    }

    function createGeometricShape() {
        const types = ['triangle', 'square', 'hexagon', 'line'];
        return {
            x: Math.random() * canvas.width,
            y: Math.random() * canvas.height,
            type: types[Math.floor(Math.random() * types.length)],
            size: Math.random() * 40 + 15,
            rotation: Math.random() * Math.PI * 2,
            rotationSpeed: (Math.random() - 0.5) * 0.008,
            opacity: Math.random() * 0.25 + 0.1,
            life: Math.random() * 2 + 1,
            maxLife: Math.random() * 2 + 1,
            vx: (Math.random() - 0.5) * 0.15,
            vy: (Math.random() - 0.5) * 0.15,
            hue: Math.random() * 60 + 170
        };
    }

    function initWavePath() {
        wavePath = [];
        const points = Math.floor(canvas.width / 30);
        for (let i = 0; i <= points; i++) {
            wavePath.push({
                x: (canvas.width / points) * i,
                baseY: canvas.height * 0.7,
                amplitude: Math.random() * 20 + 10,
                frequency: Math.random() * 0.02 + 0.01,
                phase: Math.random() * Math.PI * 2
            });
        }
    }

    function initParticles() {
        const particleCount = Math.min(35, Math.floor((canvas.width * canvas.height) / 25000));
        particles = [];
        for (let i = 0; i < particleCount; i++) {
            particles.push(createParticle());
        }

        const shapeCount = Math.min(12, Math.floor((canvas.width * canvas.height) / 70000));
        geometricShapes = [];
        for (let i = 0; i < shapeCount; i++) {
            geometricShapes.push(createGeometricShape());
        }
    }

    function updateParticles() {
        time += 0.01;

        particles.forEach(particle => {
            particle.x += particle.vx;
            particle.y += particle.vy;
            particle.life -= 0.001;

            // Add subtle floating motion
            particle.x += Math.sin(time + particle.x * 0.01) * 0.1;
            particle.y += Math.cos(time + particle.y * 0.01) * 0.05;

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

        // Update geometric shapes
        geometricShapes.forEach(shape => {
            shape.x += shape.vx;
            shape.y += shape.vy;
            shape.rotation += shape.rotationSpeed;
            shape.life -= 0.0005;

            // Wrap around
            if (shape.x < -50) shape.x = canvas.width + 50;
            if (shape.x > canvas.width + 50) shape.x = -50;
            if (shape.y < -50) shape.y = canvas.height + 50;
            if (shape.y > canvas.height + 50) shape.y = -50;

            if (shape.life <= 0) {
                Object.assign(shape, createGeometricShape());
            }
        });
    }

    function drawWavePattern() {
        const isDark = !document.documentElement.hasAttribute('data-bs-theme') || 
                      document.documentElement.getAttribute('data-bs-theme') === 'dark';

        ctx.beginPath();
        ctx.moveTo(0, canvas.height);

        wavePath.forEach((point, i) => {
            const y = point.baseY + Math.sin(time * point.frequency + point.phase) * point.amplitude;
            if (i === 0) {
                ctx.lineTo(point.x, y);
            } else {
                const prevPoint = wavePath[i - 1];
                const prevY = prevPoint.baseY + Math.sin(time * prevPoint.frequency + prevPoint.phase) * prevPoint.amplitude;
                const cpx = (prevPoint.x + point.x) / 2;
                const cpy = (prevY + y) / 2;
                ctx.quadraticCurveTo(cpx, cpy, point.x, y);
            }
        });

        ctx.lineTo(canvas.width, canvas.height);
        ctx.closePath();

        const gradient = ctx.createLinearGradient(0, canvas.height * 0.6, 0, canvas.height);
        if (isDark) {
            gradient.addColorStop(0, 'rgba(100, 255, 218, 0.08)');
            gradient.addColorStop(0.5, 'rgba(124, 58, 237, 0.06)');
            gradient.addColorStop(1, 'rgba(100, 255, 218, 0.03)');
        } else {
            gradient.addColorStop(0, 'rgba(14, 165, 233, 0.06)');
            gradient.addColorStop(0.5, 'rgba(139, 92, 246, 0.04)');
            gradient.addColorStop(1, 'rgba(14, 165, 233, 0.02)');
        }
        
        ctx.fillStyle = gradient;
        ctx.fill();
    }

    function drawGeometricShape(shape) {
        const isDark = !document.documentElement.hasAttribute('data-bs-theme') || 
                      document.documentElement.getAttribute('data-bs-theme') === 'dark';
        
        const alpha = (shape.opacity * shape.life) / shape.maxLife;
        const color = isDark ? 
            `hsla(${shape.hue}, 80%, 70%, ${alpha})` : 
            `hsla(${shape.hue - 20}, 70%, 55%, ${alpha})`;
        
        ctx.save();
        ctx.translate(shape.x, shape.y);
        ctx.rotate(shape.rotation);
        
        ctx.strokeStyle = color;
        ctx.lineWidth = 1.5;
        ctx.setLineDash([8, 4]);
        
        // Add glow effect
        ctx.shadowColor = color;
        ctx.shadowBlur = 6;
        
        switch (shape.type) {
            case 'triangle':
                ctx.beginPath();
                ctx.moveTo(0, -shape.size / 2);
                ctx.lineTo(-shape.size / 2, shape.size / 2);
                ctx.lineTo(shape.size / 2, shape.size / 2);
                ctx.closePath();
                ctx.stroke();
                break;
                
            case 'square':
                ctx.strokeRect(-shape.size / 2, -shape.size / 2, shape.size, shape.size);
                break;
                
            case 'hexagon':
                ctx.beginPath();
                for (let i = 0; i < 6; i++) {
                    const angle = (i * Math.PI) / 3;
                    const x = Math.cos(angle) * shape.size / 2;
                    const y = Math.sin(angle) * shape.size / 2;
                    if (i === 0) ctx.moveTo(x, y);
                    else ctx.lineTo(x, y);
                }
                ctx.closePath();
                ctx.stroke();
                break;
                
            case 'line':
                ctx.beginPath();
                ctx.moveTo(-shape.size / 2, 0);
                ctx.lineTo(shape.size / 2, 0);
                ctx.stroke();
                break;
        }
        
        ctx.restore();
    }

    function drawParticles() {
        const isDark = !document.documentElement.hasAttribute('data-bs-theme') || 
                      document.documentElement.getAttribute('data-bs-theme') === 'dark';
        
        ctx.clearRect(0, 0, canvas.width, canvas.height);

        // Draw subtle grid pattern
        drawGridPattern(isDark);
        
        // Draw wave pattern
        drawWavePattern();

        // Draw geometric shapes
        geometricShapes.forEach(shape => {
            drawGeometricShape(shape);
        });

        // Draw particles with enhanced glow and vibrancy
        particles.forEach(particle => {
            const alpha = (particle.opacity * particle.life) / particle.maxLife;
            const pulse = Math.sin(time * particle.pulseSpeed) * 0.3 + 0.7;
            
            ctx.beginPath();
            ctx.arc(particle.x, particle.y, particle.size * pulse, 0, Math.PI * 2);
            
            // Create vibrant glow effect
            const gradient = ctx.createRadialGradient(
                particle.x, particle.y, 0,
                particle.x, particle.y, particle.size * 4
            );
            
            if (isDark) {
                gradient.addColorStop(0, `hsla(${particle.hue}, 90%, 85%, ${alpha})`);
                gradient.addColorStop(0.2, `hsla(${particle.hue}, 90%, 70%, ${alpha * 0.8})`);
                gradient.addColorStop(0.5, `hsla(${particle.hue}, 80%, 60%, ${alpha * 0.4})`);
                gradient.addColorStop(1, `hsla(${particle.hue}, 70%, 50%, 0)`);
            } else {
                gradient.addColorStop(0, `hsla(${particle.hue - 20}, 80%, 60%, ${alpha})`);
                gradient.addColorStop(0.2, `hsla(${particle.hue - 20}, 80%, 50%, ${alpha * 0.8})`);
                gradient.addColorStop(0.5, `hsla(${particle.hue - 20}, 70%, 40%, ${alpha * 0.4})`);
                gradient.addColorStop(1, `hsla(${particle.hue - 20}, 60%, 30%, 0)`);
            }
            
            ctx.fillStyle = gradient;
            ctx.fill();
            
            // Add bright core
            ctx.beginPath();
            ctx.arc(particle.x, particle.y, particle.size * 0.3, 0, Math.PI * 2);
            ctx.fillStyle = isDark ? 
                `hsla(${particle.hue}, 100%, 95%, ${alpha * 0.9})` : 
                `hsla(${particle.hue - 20}, 90%, 70%, ${alpha * 0.9})`;
            ctx.fill();
        });

        // Draw minimal connections
        drawConnections(isDark);
    }

    function drawGridPattern(isDark) {
        const gridSize = 80;
        const opacity = isDark ? 0.04 : 0.025;
        
        ctx.strokeStyle = isDark ? `rgba(100, 255, 218, ${opacity})` : `rgba(14, 165, 233, ${opacity})`;
        ctx.lineWidth = 0.8;
        
        // Add subtle glow to grid
        ctx.shadowColor = ctx.strokeStyle;
        ctx.shadowBlur = 2;
        
        // Vertical lines
        for (let x = 0; x < canvas.width; x += gridSize) {
            ctx.beginPath();
            ctx.moveTo(x, 0);
            ctx.lineTo(x, canvas.height);
            ctx.stroke();
        }
        
        // Horizontal lines
        for (let y = 0; y < canvas.height; y += gridSize) {
            ctx.beginPath();
            ctx.moveTo(0, y);
            ctx.lineTo(canvas.width, y);
            ctx.stroke();
        }
        
        // Reset shadow
        ctx.shadowBlur = 0;
    }

    function drawConnections(isDark) {
        particles.forEach((particle, i) => {
            particles.slice(i + 1).forEach(otherParticle => {
                const dx = particle.x - otherParticle.x;
                const dy = particle.y - otherParticle.y;
                const distance = Math.sqrt(dx * dx + dy * dy);

                if (distance < 100) {
                    const opacity = (100 - distance) / 100 * 0.15;
                    const avgLife = (particle.life + otherParticle.life) / 2;
                    const avgHue = (particle.hue + otherParticle.hue) / 2;
                    
                    ctx.beginPath();
                    ctx.moveTo(particle.x, particle.y);
                    ctx.lineTo(otherParticle.x, otherParticle.y);
                    
                    if (isDark) {
                        ctx.strokeStyle = `hsla(${avgHue}, 80%, 75%, ${opacity * avgLife})`;
                    } else {
                        ctx.strokeStyle = `hsla(${avgHue - 20}, 70%, 55%, ${opacity * avgLife})`;
                    }
                    
                    ctx.lineWidth = 0.8;
                    ctx.shadowColor = ctx.strokeStyle;
                    ctx.shadowBlur = 2;
                    ctx.stroke();
                    ctx.shadowBlur = 0;
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