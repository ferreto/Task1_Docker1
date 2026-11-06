import os
import random
from pathlib import Path

from playwright.sync_api import sync_playwright


PATH_TO_VIDEO_FOLDER = "./video"

# Module-level reference so the ShowCallout helper can access the active page.
_ACTIVE_PAGE = None


# ---------------------------------------------------------------------------
# Injected script: SVG cursor + yellow highlight + ripple + callout support
# ---------------------------------------------------------------------------
CURSOR_SCRIPT = r"""
(() => {
  const INSTALL = () => {
    if (!document.documentElement) { setTimeout(INSTALL, 0); return; }
    if (document.getElementById('__pw_cursor_root')) return;

    const root = document.createElement('div');
    root.id = '__pw_cursor_root';
    root.style.cssText =
      'position:fixed;inset:0;pointer-events:none;z-index:2147483647;overflow:hidden;';
    document.documentElement.appendChild(root);

    // --- Yellow semi-transparent highlight (radius ~32px => 64px diameter)
    const circle = document.createElement('div');
    circle.style.cssText =
      'position:fixed;width:64px;height:64px;border-radius:50%;' +
      'background:rgba(255,235,59,0.45);transform:translate(-50%,-50%);' +
      'left:-9999px;top:-9999px;pointer-events:none;';
    root.appendChild(circle);

    // --- SVG arrow cursor (white fill, black outline, ~32x32)
    const svgNS = 'http://www.w3.org/2000/svg';
    const svg = document.createElementNS(svgNS, 'svg');
    svg.setAttribute('width', '32');
    svg.setAttribute('height', '32');
    svg.style.cssText =
      'position:fixed;left:-9999px;top:-9999px;pointer-events:none;' +
      'filter:drop-shadow(0 0 2px rgba(0,0,0,0.4));';
    const path = document.createElementNS(svgNS, 'path');
    path.setAttribute('d', 'M4,4 L4,26 L11,20 L15,29 L19,27 L15,18 L24,18 Z');
    path.setAttribute('fill', 'white');
    path.setAttribute('stroke', 'black');
    path.setAttribute('stroke-width', '2');
    path.setAttribute('stroke-linejoin', 'round');
    svg.appendChild(path);
    root.appendChild(svg);

    window.__pw_cursor_move = (x, y) => {
      circle.style.left = x + 'px';
      circle.style.top  = y + 'px';
      // arrow tip is at (4,4) inside the 32x32 svg
      svg.style.left = (x - 4) + 'px';
      svg.style.top  = (y - 4) + 'px';
    };

    window.__pw_cursor_ripple = (x, y) => {
      const r = document.createElement('div');
      r.style.cssText =
        'position:fixed;left:' + x + 'px;top:' + y + 'px;' +
        'width:10px;height:10px;border-radius:50%;' +
        'background:rgba(255,235,59,0.7);transform:translate(-50%,-50%);' +
        'pointer-events:none;' +
        'transition:width 0.5s ease-out,height 0.5s ease-out,opacity 0.5s ease-out;';
      root.appendChild(r);
      requestAnimationFrame(() => {
        r.style.width = '72px';
        r.style.height = '72px';
        r.style.opacity = '0';
      });
      setTimeout(() => r.remove(), 600);
    };

    window.__pw_cursor_callout = (text, bg, fg, fade) => {
      const box = document.createElement('div');
      box.style.cssText =
        'position:fixed;left:60px;bottom:60px;' +
        'background:' + bg + ';color:' + fg + ';' +
        'font-family:sans-serif;font-size:16pt;padding:10px 16px;' +
        'border-radius:8px;pointer-events:none;opacity:0;' +
        'transition:opacity ' + fade + 'ms ease-in-out;' +
        'box-shadow:0 2px 8px rgba(0,0,0,0.3);max-width:70vw;z-index:1;';
      box.textContent = text;
      root.appendChild(box);
      requestAnimationFrame(() => { box.style.opacity = '1'; });
      setTimeout(() => { box.style.opacity = '0'; }, fade + 2000);
      setTimeout(() => { box.remove(); }, fade * 2 + 2500);
    };

    document.addEventListener(
      'mousemove',
      (e) => window.__pw_cursor_move(e.clientX, e.clientY),
      true,
    );
    document.addEventListener(
      'mousedown',
      (e) => window.__pw_cursor_ripple(e.clientX, e.clientY),
      true,
    );
  };

  INSTALL();
})();
"""


# ---------------------------------------------------------------------------
# Python-side helpers
# ---------------------------------------------------------------------------
def _smooth_move(page, from_xy, to_xy, duration_ms=700, steps=30):
    """Move the mouse from `from_xy` to `to_xy` along a smooth (smoothstep)
    eased path so the motion is visible on video."""
    x1, y1 = from_xy
    x2, y2 = to_xy
    for i in range(1, steps + 1):
        t = i / steps
        e = t * t * (3 - 2 * t)  # smoothstep easing
        page.mouse.move(x1 + (x2 - x1) * e, y1 + (y2 - y1) * e)
        page.wait_for_timeout(max(5, duration_ms // steps))
    return (x2, y2)


# Candidates for the search submit button, most specific first.
SEARCH_BUTTON_SELECTORS = (
    'button[type="submit"][aria-label="Search"]',
    'button[aria-label="Search"]',
    'form button[type="submit"]',
    'form input[type="submit"]',
)


def _find_visible(page, selectors, timeout_ms=8000):
    """Return the first *visible* element matching any of `selectors`, or
    None if nothing visible shows up within `timeout_ms`.

    Every match of every selector is checked (not just `.first`), because a
    hidden element that comes earlier in the DOM would otherwise hide the
    real, visible button.
    """
    waited = 0
    while True:
        for sel in selectors:
            try:
                for loc in page.locator(sel).all():
                    if loc.is_visible() and loc.bounding_box():
                        return loc
            except Exception:
                pass
        if waited >= timeout_ms:
            return None
        page.wait_for_timeout(250)
        waited += 250


def _human_type(page, text, min_delay=40, max_delay=130):
    """Type text character-by-character with randomized, human-like delays."""
    for ch in text:
        page.keyboard.type(ch)
        page.wait_for_timeout(random.randint(min_delay, max_delay))


# DuckDuckGo's own bot-check UI.
CAPTCHA_SELECTORS = (
    "form[action*='anomaly']",
    "input[name='challenge']",
    "div[id*='anomaly']",
    "div[class*='captcha']",
    "[data-testid='anomaly-modal']",
)
# Wording of DuckDuckGo's own challenge page. Generic words such as
# "captcha", "anomaly" or "unusual traffic" are deliberately NOT used: they
# can appear in the text of ordinary search results (e.g. snippets of pages
# about CAPTCHAs) and would make a healthy results page look blocked.
CAPTCHA_TEXT_MARKERS = (
    "bots use duckduckgo too",
    "confirm this search was made by a human",
)


def _is_captcha_shown(page):
    """Return True if DuckDuckGo is showing a CAPTCHA / bot-block page.

    Callers must check for real search results first: a page that already
    shows results is never a CAPTCHA page, whatever its text says.
    """
    for sel in CAPTCHA_SELECTORS:
        try:
            if page.locator(sel).first.count() > 0:
                return True
        except Exception:
            pass
    try:
        text = page.locator("body").inner_text(timeout=2000).lower()
    except Exception:
        return False
    return any(m in text for m in CAPTCHA_TEXT_MARKERS)


def _restore_cursor(page, pos):
    """Redraw the injected cursor at `pos`.

    A page navigation replaces the document, so the injected cursor is lost
    (or sits at its off-screen start position) until the next real mouse
    move. Playwright's real mouse, however, stays where it was, so redrawing
    the cursor at the last known position makes it continue from there
    instead of jumping somewhere else.
    """
    try:
        page.evaluate(
            "([x, y]) => { if (window.__pw_cursor_move) "
            "window.__pw_cursor_move(x, y); }",
            [pos[0], pos[1]],
        )
    except Exception:
        pass  # e.g. the document is being replaced right now


def _discard_video(video_obj):
    """Delete the recording of a failed run so no junk file is left behind."""
    try:
        video_obj.delete()
    except Exception as e:
        print(f"[qscript] could not delete the failed recording: {e}")


def ShowCallout(text, bgColor="yellow", textColor="black", fade=2000):
    """Display a yellow callout with text in the currently active page.

    Parameters match the task specification exactly. If no active page is
    available (helper called outside CaptureVideo_Magento) it is a no-op.
    """
    page = _ACTIVE_PAGE
    if page is None:
        return
    try:
        page.evaluate(
            "([t,b,f,d]) => { if (window.__pw_cursor_callout) "
            "window.__pw_cursor_callout(t, b, f, d); }",
            [text, bgColor, textColor, fade],
        )
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Scenario
# ---------------------------------------------------------------------------
def _run_scenario(page, url, city):
    """Search for Magento developers in `city` and open the first result.

    Raises on any failure; the caller takes care of the recording.
    """
    # --- 1. Open page -------------------------------------------------
    page.goto(url, wait_until="domcontentloaded", timeout=30000)
    page.wait_for_timeout(500)

    if _is_captcha_shown(page):
        raise RuntimeError(
            "DuckDuckGo showed a CAPTCHA right away - try a different "
            "IP/VPN or wait until the block expires."
        )

    # --- 2. Place cursor on screen and show a callout ----------------
    pos = (640, 360)
    page.mouse.move(*pos)
    page.wait_for_timeout(200)

    ShowCallout(f"Searching Magento developers in {city}", fade=1500)
    page.wait_for_timeout(400)

    # --- 3. Move to the search box -----------------------------------
    search_box = page.locator('textarea[name="q"]').first
    search_box.wait_for(state="visible", timeout=15000)
    bb = search_box.bounding_box()
    target = (bb["x"] + bb["width"] / 2, bb["y"] + bb["height"] / 2)
    pos = _smooth_move(page, pos, target, duration_ms=800, steps=40)
    page.wait_for_timeout(200)

    # --- 4. Click and type the query ---------------------------------
    page.mouse.click(*pos)
    page.wait_for_timeout(300)

    _human_type(page, f"Magento developer in {city}")
    page.wait_for_timeout(400)

    # --- 5. Move to the Search button and click it -------------------
    # The search is always submitted with a visible mouse click.
    # The Enter key is deliberately NOT used as a fallback: it is
    # invisible in the video, so a missing button is an error.
    submit = _find_visible(page, SEARCH_BUTTON_SELECTORS)
    sb = submit.bounding_box() if submit is not None else None
    if not sb:
        raise RuntimeError("Search button not found")
    target = (sb["x"] + sb["width"] / 2, sb["y"] + sb["height"] / 2)
    pos = _smooth_move(page, pos, target, duration_ms=700, steps=30)
    page.wait_for_timeout(300)  # let the viewer see the cursor on it
    page.mouse.click(*pos)

    # --- 6. Wait for the results page --------------------------------
    selectors = [
        'a[data-testid="result-title-a"]',
        "#links .result__a",
        "a.result__a",
    ]
    first_result = None
    for attempt in range(120):  # poll every 250 ms, up to ~30 seconds
        # The click navigates to a new document, which wipes the injected
        # cursor. Redraw it where the real mouse still is, so it stays
        # visible and does not jump while the results load.
        _restore_cursor(page, pos)

        for sel in selectors:
            try:
                loc = page.locator(sel).first
                if loc.count() > 0 and loc.bounding_box():
                    first_result = loc
                    break
            except Exception:
                pass
        if first_result is not None:
            break

        # Only look for a CAPTCHA while there are no results yet (and only
        # every ~1 s, because reading the whole page text is not free).
        if attempt % 4 == 3 and _is_captcha_shown(page):
            try:
                page.screenshot(path="debug_captcha.png")
                print("[qscript] CAPTCHA screenshot: debug_captcha.png")
            except Exception:
                pass
            raise RuntimeError(
                "DuckDuckGo showed a CAPTCHA after the search was "
                "submitted. Switch IP/VPN or try again later."
            )
        page.wait_for_timeout(250)

    if first_result is None:
        try:
            page.screenshot(path="debug_no_results.png")
            print("[qscript] No-results screenshot: debug_no_results.png")
        except Exception:
            pass
        raise RuntimeError("No search results appeared")

    # --- 7. Move to first result and click ---------------------------
    # The cursor continues from where the Search button was clicked.
    page.wait_for_timeout(300)
    rb = first_result.bounding_box()
    target = (rb["x"] + rb["width"] / 2, rb["y"] + rb["height"] / 2)
    pos = _smooth_move(page, pos, target, duration_ms=700, steps=30)
    page.wait_for_timeout(300)
    page.mouse.click(*pos)
    page.wait_for_timeout(1500)


# ---------------------------------------------------------------------------
# Main public API
# ---------------------------------------------------------------------------
def CaptureVideo_Magento(
    url,
    company_name,
    language,
    country,
    postcode,
    city,
    street,
    house_no,
):
    """Run the sample scenario for one CSV row and save its video.

    Returns the absolute path to the resulting .webm file, or None on failure.
    A failed run leaves no video file behind.
    """
    global _ACTIVE_PAGE

    Path(PATH_TO_VIDEO_FOLDER).mkdir(parents=True, exist_ok=True)

    src = None
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(
                # DuckDuckGo blocks headless Chromium (shows CAPTCHA instead
                # of results). Use a visible window; Playwright still records
                # the page content to video.
                headless=False,
                args=["--disable-blink-features=AutomationControlled"],
            )
            context = None
            video_obj = None
            succeeded = False
            try:
                # No user_agent override: the real User-Agent of the
                # installed Chromium always matches its client hints.
                context = browser.new_context(
                    record_video_dir=PATH_TO_VIDEO_FOLDER,
                    record_video_size={"width": 1280, "height": 720},
                    viewport={"width": 1280, "height": 720},
                )
                context.add_init_script(CURSOR_SCRIPT)
                page = context.new_page()
                _ACTIVE_PAGE = page
                video_obj = page.video

                _run_scenario(page, url, city)
                succeeded = True
            finally:
                # Always close the context: that is what flushes the
                # recording to disk. Then keep it (success) or delete it
                # (failure) so no hash-named junk files pile up.
                if context is not None:
                    try:
                        context.close()
                    except Exception:
                        pass
                if video_obj is not None:
                    if succeeded:
                        try:
                            src = str(video_obj.path())
                        except Exception as e:
                            print(f"[qscript] video.path() failed: {e}")
                    else:
                        _discard_video(video_obj)
                try:
                    browser.close()
                except Exception:
                    pass

    except Exception as e:
        print(f"[qscript] CaptureVideo_Magento failed: {e}")
        return None
    finally:
        _ACTIVE_PAGE = None

    # --- 9. Rename to <company_name>.webm and return the path ----------
    if not src or not os.path.exists(src):
        return None

    safe = "".join(
        c if c.isalnum() or c in "-_ " else "_" for c in (company_name or "")
    ).strip() or "video"
    out_path = os.path.abspath(
        os.path.join(PATH_TO_VIDEO_FOLDER, f"{safe}.webm")
    )
    try:
        os.replace(src, out_path)
    except Exception as e:
        print(f"[qscript] rename failed: {e}")
        return os.path.abspath(src)
    return out_path
