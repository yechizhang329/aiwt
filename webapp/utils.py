import streamlit as st


# CSS injected via st.markdown applies globally to the Streamlit document.
# Sidebar nav is in the same document so this works, but only for styling.
_HIDE_NAV_CSS = """
<style>
[data-testid="stSidebarNav"] { display: none !important; }
</style>
"""

# JS via st.components runs in an iframe on the same origin, so window.parent
# gives access to the host Streamlit document. We use a MutationObserver to
# re-hide the nav after Streamlit's reactive rerenders restore it.
_HIDE_NAV_JS = """
<script>
(function() {
    function hide() {
        try {
            var doc = window.parent.document;
            var el = doc.querySelector('[data-testid="stSidebarNav"]');
            if (el) el.style.setProperty('display', 'none', 'important');
            // Also inject a persistent style rule into the parent document
            if (!doc.getElementById('_hide_nav_style')) {
                var s = doc.createElement('style');
                s.id = '_hide_nav_style';
                s.textContent = '[data-testid="stSidebarNav"]{display:none!important}';
                doc.head.appendChild(s);
            }
        } catch(e) {}
    }
    hide();
    new MutationObserver(hide).observe(window.parent.document.body,
        {childList: true, subtree: true});
})();
</script>
"""


def hide_default_nav():
    st.markdown(_HIDE_NAV_CSS, unsafe_allow_html=True)
    st.components.v1.html(_HIDE_NAV_JS, height=0)
