"""
sidh_browser.py
===============
Script ka apna Chrome window kholta hai (alag profile: sidh_browser_profile/).
User usme SIDH portal par login karta hai; script token khud
localStorage.access_token se padh leta hai. Token expire ho to page reload
karke naya token leta hai, warna login ka wait karta hai.

Token kabhi print / file mein save nahi hota.
"""

import base64
import json
import time
from datetime import datetime

START_URL = "https://www.skillindiadigital.gov.in/home"  # browser yahin khulta hai (login)
ADMIN_URL = "https://admin.skillindiadigital.gov.in/admin-profile/assessmentagency"
ADMIN_HOST = "admin.skillindiadigital.gov.in"


def token_expiry(token):
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        exp = json.loads(base64.urlsafe_b64decode(payload)).get("exp")
        return datetime.fromtimestamp(exp) if exp else None
    except Exception:
        return None


def expired(token, margin_s=120):
    exp = token_expiry(token)
    return bool(exp) and (exp - datetime.now()).total_seconds() < margin_s


class PortalBrowser:
    def __init__(self, profile_dir="sidh_browser_profile"):
        from playwright.sync_api import sync_playwright

        self.pw = sync_playwright().start()
        opts = dict(headless=False, no_viewport=True, args=["--start-maximized"])
        try:
            self.ctx = self.pw.chromium.launch_persistent_context(profile_dir, channel="chrome", **opts)
        except Exception:
            self.ctx = self.pw.chromium.launch_persistent_context(profile_dir, **opts)
        self.page = self.ctx.pages[0] if self.ctx.pages else self.ctx.new_page()
        try:
            self.page.goto(START_URL, wait_until="domcontentloaded", timeout=60000)
        except Exception:
            pass

    def _pages(self):
        try:
            pages = self.ctx.pages
        except Exception:
            pages = []
        if not pages:  # user ne window band kar di -> nayi kholo
            self.page = self.ctx.new_page()
            try:
                self.page.goto(START_URL, wait_until="domcontentloaded", timeout=60000)
            except Exception:
                pass
            pages = [self.page]
        return pages

    def _read(self):
        for p in self._pages():
            if ADMIN_HOST in (p.url or ""):
                try:
                    t = p.evaluate("localStorage.getItem('access_token')")
                except Exception:
                    t = None
                if t:
                    return t
        return None

    def _reload_admin(self):
        for p in self._pages():
            if ADMIN_HOST in (p.url or ""):
                try:
                    p.reload(wait_until="domcontentloaded", timeout=60000)
                    return
                except Exception:
                    pass
        try:
            self.page.goto(ADMIN_URL, wait_until="domcontentloaded", timeout=60000)
        except Exception:
            pass

    def token(self, bad=None):
        """Valid token do. Na mile to login ka wait (jitna time lage)."""
        reloaded = False
        asked = 0.0
        while True:
            t = self._read()
            if t and t != bad and not expired(t):
                exp = token_expiry(t)
                if asked:
                    print("\n[OK] Login mil gaya, kaam aage chal raha hai.", flush=True)
                if exp:
                    print(f"[*] Token valid till: {exp:%d-%m-%Y %H:%M}", flush=True)
                return t
            if (t == bad or (t and expired(t))) and not reloaded:
                self._reload_admin()  # portal kabhi khud naya token de deta hai
                reloaded = True
                continue
            if time.time() - asked > 300:
                print("\a\n[!!] Script wali Chrome window mein SIDH portal par LOGIN karo.", flush=True)
                print("     skillindiadigital.gov.in par login karo, phir Admin Dashboard", flush=True)
                print("     (admin.skillindiadigital.gov.in) khulna chahiye. Script khud aage badh jayegi.", flush=True)
                asked = time.time()
            try:
                self._pages()[0].wait_for_timeout(3000)
            except Exception:
                time.sleep(3)

    def close(self):
        try:
            self.ctx.close()
            self.pw.stop()
        except Exception:
            pass
