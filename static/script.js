// Character counter for tweet box
document.addEventListener('DOMContentLoaded', function() {
    const textarea = document.querySelector('.tweet-box textarea');
    const charCount = document.querySelector('.char-count');
    
    if (textarea && charCount) {
        textarea.addEventListener('input', function() {
            const remaining = 280 - this.value.length;
            charCount.textContent = remaining;
            charCount.style.color = remaining < 20 ? '#ff4444' : '#8899a6';
        });
    }

    // Mobile menu toggle
    const mobileMenuToggle = document.querySelector('.mobile-menu-toggle');
    const navLinks = document.querySelector('.nav-links');

    if (mobileMenuToggle && navLinks) {
        mobileMenuToggle.addEventListener('click', function() {
            mobileMenuToggle.classList.toggle('active');
            navLinks.classList.toggle('active');
        });

        // Close mobile menu when clicking outside
        document.addEventListener('click', function(event) {
            if (!mobileMenuToggle.contains(event.target) && !navLinks.contains(event.target)) {
                mobileMenuToggle.classList.remove('active');
                navLinks.classList.remove('active');
            }
        });

        // Close mobile menu when clicking a link
        navLinks.addEventListener('click', function(event) {
            if (event.target.tagName === 'A') {
                mobileMenuToggle.classList.remove('active');
                navLinks.classList.remove('active');
            }
        });

        // Close mobile menu on window resize to desktop
        window.addEventListener('resize', function() {
            if (window.innerWidth > 768) {
                mobileMenuToggle.classList.remove('active');
                navLinks.classList.remove('active');
            }
        });
    }
});

// Toggle reply form
function toggleReplyForm(tweetId) {
    const form = document.getElementById('reply-form-' + tweetId);
    if (form) {
        form.style.display = form.style.display === 'none' ? 'block' : 'none';
    }
}