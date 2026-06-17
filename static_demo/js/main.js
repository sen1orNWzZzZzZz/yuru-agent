/**
 * 智能旅游多Agent规划系统 - 前端通用JS
 */

// 浮动粒子生成
document.addEventListener('DOMContentLoaded', () => {
    const container = document.getElementById('particles');
    if (!container) return;

    for (let i = 0; i < 8; i++) {
        const particle = document.createElement('div');
        particle.style.cssText = `
            position: absolute;
            width: ${2 + Math.random() * 3}px;
            height: ${2 + Math.random() * 3}px;
            border-radius: 50%;
            background: #D98A5B;
            opacity: ${0.05 + Math.random() * 0.1};
            left: ${Math.random() * 100}%;
            top: ${Math.random() * 100}%;
            animation: float-particle ${4 + Math.random() * 6}s ease-in-out infinite;
            animation-delay: ${-Math.random() * 8}s;
        `;
        container.appendChild(particle);
    }
});
