// Zenus - Personal Website Script
document.querySelector('.year').textContent = new Date().getFullYear();

// Add subtle entrance animation
document.addEventListener('DOMContentLoaded', () => {
  const container = document.querySelector('.container');
  container.style.opacity = '0';
  container.style.transform = 'translateY(20px)';
  container.style.transition = 'opacity 0.6s ease, transform 0.6s ease';

  requestAnimationFrame(() => {
    container.style.opacity = '1';
    container.style.transform = 'translateY(0)';
  });
});
