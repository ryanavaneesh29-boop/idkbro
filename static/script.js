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

    const threadComposer = document.querySelector('.thread-compose textarea');
    const threadCharCount = document.querySelector('.thread-compose .char-count');

    if (threadComposer && threadCharCount) {
        const updateThreadCount = function() {
            const remaining = 280 - threadComposer.value.length;
            threadCharCount.textContent = remaining;
            threadCharCount.style.color = remaining < 20 ? '#ff4444' : '#8899a6';
        };

        threadComposer.addEventListener('input', updateThreadCount);
        updateThreadCount();
    }

    const directMessageComposer = document.getElementById('content');
    const directMessageCharCount = document.getElementById('char-count');

    if (directMessageComposer && directMessageCharCount) {
        const updateDirectMessageCount = function() {
            directMessageCharCount.textContent = directMessageComposer.value.length;
            directMessageCharCount.classList.toggle('over-limit', directMessageComposer.value.length > 280);
        };

        directMessageComposer.addEventListener('input', updateDirectMessageCount);
        updateDirectMessageCount();
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

    const deleteModal = document.getElementById('delete-confirm-modal');
    const deleteMessage = document.getElementById('delete-confirm-message');
    const deleteSubmit = document.getElementById('delete-confirm-submit');
    let pendingDeleteForm = null;

    if (deleteModal && deleteMessage && deleteSubmit) {
        const openDeleteModal = function(form, message) {
            pendingDeleteForm = form;
            deleteMessage.textContent = message || 'Are you sure you want to delete this?';
            deleteModal.classList.remove('is-hidden');
            deleteModal.setAttribute('aria-hidden', 'false');
            deleteSubmit.focus();
        };

        const closeDeleteModal = function() {
            pendingDeleteForm = null;
            deleteModal.classList.add('is-hidden');
            deleteModal.setAttribute('aria-hidden', 'true');
        };

        document.querySelectorAll('.delete-form').forEach(function(form) {
            form.addEventListener('submit', function(event) {
                const button = form.querySelector('.delete-btn');
                if (!button || form.dataset.confirmed === 'true') {
                    return;
                }
                event.preventDefault();
                openDeleteModal(form, button.dataset.confirmMessage);
            });
        });

        deleteSubmit.addEventListener('click', function() {
            if (!pendingDeleteForm) {
                return;
            }
            pendingDeleteForm.dataset.confirmed = 'true';
            pendingDeleteForm.submit();
        });

        document.querySelectorAll('[data-confirm-cancel]').forEach(function(button) {
            button.addEventListener('click', closeDeleteModal);
        });

        document.addEventListener('keydown', function(event) {
            if (event.key === 'Escape' && deleteModal.getAttribute('aria-hidden') === 'false') {
                closeDeleteModal();
            }
        });
    }
});

// Toggle reply form
function toggleReplyForm(tweetId) {
    const form = document.getElementById('reply-form-' + tweetId);
    if (form) {
        form.classList.toggle('is-hidden');
    }
}
