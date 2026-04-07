/* QuantBot Platform — Client JS */
// Close modals on escape
document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape') {
    document.querySelectorAll('.deposit-modal.active').forEach(m => m.classList.remove('active'));
  }
});
// Close modal on backdrop click
document.querySelectorAll('.deposit-modal').forEach(m => {
  m.addEventListener('click', (e) => { if (e.target === m) m.classList.remove('active'); });
});
