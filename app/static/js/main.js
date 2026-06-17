/**
 * 灵动旅心 V3 - 前端交互
 * 保持克制：仅添加细腻的入场与反馈，避免喧宾夺主
 */

document.addEventListener('DOMContentLoaded', () => {
    // 导航当前页高亮已在服务端模板中处理
    // 此处仅处理全局细微交互

    // 为所有按钮添加点击涟漪反馈
    document.querySelectorAll('.btn').forEach(btn => {
        btn.addEventListener('click', function(e) {
            if (this.classList.contains('btn-ghost')) return;
            const rect = this.getBoundingClientRect();
            const ripple = document.createElement('span');
            ripple.style.cssText = `
                position: absolute;
                border-radius: 50%;
                transform: scale(0);
                animation: ripple 0.5s linear;
                background-color: rgba(255,255,255,0.25);
                pointer-events: none;
                width: 100px;
                height: 100px;
                left: ${e.clientX - rect.left - 50}px;
                top: ${e.clientY - rect.top - 50}px;
            `;
            this.style.position = 'relative';
            this.style.overflow = 'hidden';
            this.appendChild(ripple);
            setTimeout(() => ripple.remove(), 500);
        });
    });

    // 滚动时卡片细微浮现
    const observer = new IntersectionObserver((entries) => {
        entries.forEach(entry => {
            if (entry.isIntersecting) {
                entry.target.style.opacity = '1';
                entry.target.style.transform = 'translateY(0)';
                observer.unobserve(entry.target);
            }
        });
    }, { threshold: 0.1 });

    document.querySelectorAll('.feature-card, .card, .status-card').forEach(el => {
        if (!el.classList.contains('animate-fade-up')) {
            el.style.opacity = '0';
            el.style.transform = 'translateY(12px)';
            el.style.transition = 'opacity 0.6s ease, transform 0.6s ease';
            observer.observe(el);
        }
    });
});

// 全局涟漪动画关键帧
const style = document.createElement('style');
style.textContent = `
    @keyframes ripple {
        to { transform: scale(2.5); opacity: 0; }
    }
`;
document.head.appendChild(style);
