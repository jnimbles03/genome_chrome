const canvas = document.getElementById('gravityCanvas');
const ctx = canvas.getContext('2d');

let width, height;
let particles = [];
const particleCount = 100;

// Mouse state
const mouse = { x: null, y: null };
let isMouseActive = false;

// Resize handling
function resize() {
    width = canvas.width = window.innerWidth;
    height = canvas.height = window.innerHeight;
}

window.addEventListener('resize', resize);
resize();

// Mouse tracking
window.addEventListener('mousemove', (e) => {
    mouse.x = e.clientX;
    mouse.y = e.clientY;
    isMouseActive = true;

    // Smooth tilt effect for the card
    const card = document.querySelector('.glass-card');
    const xAxis = (window.innerWidth / 2 - e.pageX) / 25;
    const yAxis = (window.innerHeight / 2 - e.pageY) / 25;
    card.style.transform = `rotateY(${xAxis}deg) rotateX(${yAxis}deg)`;
});

window.addEventListener('mouseout', () => {
    isMouseActive = false;
    const card = document.querySelector('.glass-card');
    card.style.transform = `rotateY(0deg) rotateX(0deg)`;
});

class Particle {
    constructor() {
        this.reset(true);
    }

    reset(randomStart = false) {
        this.x = Math.random() * width;
        this.y = Math.random() * height;
        this.vx = (Math.random() - 0.5) * 0.5;
        this.vy = (Math.random() - 0.5) * 0.5;
        this.size = Math.random() * 2 + 1;
        this.color = Math.random() > 0.5 ? '#6366f1' : '#22d3ee'; // Primary or Accent
        this.originalSize = this.size;
        this.friction = 0.95;
    }

    update() {
        // Basic movement
        this.x += this.vx;
        this.y += this.vy;

        // Mouse interaction (Antigravity)
        if (isMouseActive) {
            const dx = mouse.x - this.x;
            const dy = mouse.y - this.y;
            const distance = Math.sqrt(dx * dx + dy * dy);
            const forceDistance = 200;

            if (distance < forceDistance) {
                const forceDirectionX = dx / distance;
                const forceDirectionY = dy / distance;
                const force = (forceDistance - distance) / forceDistance;

                // Push away from mouse (Antigravity)
                const directionX = forceDirectionX * force * 2; // Strength
                const directionY = forceDirectionY * force * 2;

                this.vx -= directionX;
                this.vy -= directionY;

                // Grow slightly when interacting
                this.size = Math.min(this.originalSize * 2, 5);
            } else {
                this.size = Math.max(this.size * 0.95, this.originalSize);
            }
        }

        // Friction to calm them down
        this.vx *= 0.99;
        this.vy *= 0.99;

        // Bounce off walls
        if (this.x < 0 || this.x > width) this.vx *= -1;
        if (this.y < 0 || this.y > height) this.vy *= -1;
    }

    draw() {
        ctx.beginPath();
        ctx.arc(this.x, this.y, this.size, 0, Math.PI * 2);
        ctx.fillStyle = this.color;
        ctx.shadowBlur = 15;
        ctx.shadowColor = this.color;
        ctx.globalAlpha = 0.6;
        ctx.fill();
        ctx.globalAlpha = 1;
    }
}

// Connect particles with lines if close
function connect() {
    for (let a = 0; a < particles.length; a++) {
        for (let b = a; b < particles.length; b++) {
            const dx = particles[a].x - particles[b].x;
            const dy = particles[a].y - particles[b].y;
            const distance = Math.sqrt(dx * dx + dy * dy);

            if (distance < 150) {
                const opacity = 1 - (distance / 150);
                ctx.strokeStyle = `rgba(99, 102, 241, ${opacity * 0.2})`;
                ctx.lineWidth = 1;
                ctx.beginPath();
                ctx.moveTo(particles[a].x, particles[a].y);
                ctx.lineTo(particles[b].x, particles[b].y);
                ctx.stroke();
            }
        }
    }
}

// Init
function init() {
    particles = [];
    for (let i = 0; i < particleCount; i++) {
        particles.push(new Particle());
    }
}

// Animation Loop
function animate() {
    requestAnimationFrame(animate);
    ctx.clearRect(0, 0, width, height);

    particles.forEach(p => {
        p.update();
        p.draw();
    });
    connect();
}

init();
animate();
