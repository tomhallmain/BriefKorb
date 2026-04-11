"""
HTML processing utilities for email content display
"""

import re
import html
import base64
from typing import Optional


def _rgb_to_hex(match: re.Match) -> str:
    """Convert an rgb(r, g, b) or rgba(r, g, b, a) match to #rrggbb hex.

    Qt's QTextHtmlParser/QCssParser only understands named colours and #rrggbb
    hex — functional rgb/rgba syntax produces "Unknown color" or
    "Specified color without alpha value but alpha given" warnings and the
    colour is silently dropped.  Alpha is discarded because Qt has no way to
    honour it in this context.
    """
    r, g, b = int(match.group(1)), int(match.group(2)), int(match.group(3))
    return f'#{r:02x}{g:02x}{b:02x}'


def sanitize_html(html_content: str) -> str:
    """Sanitize HTML content to fix font size issues and enable image loading

    Args:
        html_content: Raw HTML content from email

    Returns:
        Sanitized HTML with fixed font sizes and processed images
    """
    # Convert rgb(...) and rgba(...) color values to #rrggbb hex.
    # Qt's QTextHtmlParser understands only named colours and #rrggbb hex;
    # rgb() produces "Unknown color name" warnings and rgba() produces
    # "Specified color without alpha value but alpha given" warnings — in both
    # cases the colour is silently dropped.  Match rgba first so the 'a'
    # variant is consumed before the plain rgb pattern can partially match it.
    # rgba(...) — parenthesised form with alpha channel.
    html_content = re.sub(
        r'rgba\(\s*(\d{1,3})%?\s*,\s*(\d{1,3})%?\s*,\s*(\d{1,3})%?\s*,[^)]*\)',
        _rgb_to_hex,
        html_content,
        flags=re.IGNORECASE,
    )
    # rgb(...) — parenthesised form, with or without a (non-standard) fourth
    # alpha value.  Some senders write rgb(0,0,0,.1) instead of rgba(0,0,0,.1).
    html_content = re.sub(
        r'rgb\(\s*(\d{1,3})\s*,\s*(\d{1,3})\s*,\s*(\d{1,3})(?:\s*,[^)]*)?\s*\)',
        _rgb_to_hex,
        html_content,
        flags=re.IGNORECASE,
    )
    # rgb r,g,b[,a] — no-parentheses space-separated form emitted by some
    # email clients.  Qt logs these as 'rgb r,g,b,a' and rejects the alpha.
    html_content = re.sub(
        r'rgb\s+(\d{1,3})\s*,\s*(\d{1,3})\s*,\s*(\d{1,3})(?:\s*,\s*[\d.]+)?',
        _rgb_to_hex,
        html_content,
        flags=re.IGNORECASE,
    )

    # Fix invalid font sizes (0 or negative pixel sizes)
    # Replace font-size: 0, font-size:0px, font-size:0pt, etc.
    html_content = re.sub(
        r'font-size\s*:\s*0+(\.\d+)?(px|pt|em|rem|%)?',
        'font-size: 12px',
        html_content,
        flags=re.IGNORECASE
    )
    
    # Fix negative font sizes
    html_content = re.sub(
        r'font-size\s*:\s*-\d+(\.\d+)?(px|pt|em|rem|%)?',
        'font-size: 12px',
        html_content,
        flags=re.IGNORECASE
    )
    
    # Fix font-size in style attributes that might have 0 values
    def fix_style_font_size(match):
        style = match.group(1)
        # Replace any font-size: 0 or negative values
        style = re.sub(
            r'font-size\s*:\s*0+(\.\d+)?(px|pt|em|rem|%)?',
            'font-size: 12px',
            style,
            flags=re.IGNORECASE
        )
        style = re.sub(
            r'font-size\s*:\s*-\d+(\.\d+)?(px|pt|em|rem|%)?',
            'font-size: 12px',
            style,
            flags=re.IGNORECASE
        )
        return f'style="{style}"'
    
    html_content = re.sub(r'style="([^"]*)"', fix_style_font_size, html_content, flags=re.IGNORECASE)
    
    # Process images - ensure data URIs work and handle external URLs
    html_content = _process_images(html_content)
    
    # Wrap in a container with base styles to ensure proper rendering
    # This also helps with image loading context and fixes font issues
    if not html_content.strip().startswith('<html'):
        html_content = f'''<html>
<head>
<style>
    body {{
        font-family: Arial, sans-serif;
        font-size: 14px;
        line-height: 1.5;
        color: #ffffff;
        background-color: #1e201f;
        margin: 0;
        padding: 10px;
    }}
    * {{
        font-size: inherit;
    }}
    img {{
        max-width: 100%;
        height: auto;
        display: block;
    }}
    /* Override any zero font sizes */
    [style*="font-size: 0"],
    [style*="font-size:0"] {{
        font-size: 14px !important;
    }}
</style>
</head>
<body>
{html_content}
</body>
</html>'''
    
    return html_content


def _process_images(html_content: str) -> str:
    """Process image tags in HTML content
    
    - Ensures data URIs are properly formatted
    - Downloads external images and converts to data URIs
    - Adds proper styling to images
    """
    def process_image_tag(match):
        img_tag = match.group(0)
        # If it's already a data URI, ensure it's properly formatted
        if 'data:' in img_tag:
            # Data URIs should work natively in QTextEdit
            # Just ensure proper styling
            if 'style=' not in img_tag or 'max-width' not in img_tag:
                if 'style=' in img_tag:
                    img_tag = re.sub(
                        r'style="([^"]*)"',
                        r'style="\1; max-width: 100%; height: auto;"',
                        img_tag,
                        flags=re.IGNORECASE
                    )
                else:
                    img_tag = img_tag.replace('<img', '<img style="max-width: 100%; height: auto;"')
            return img_tag
        
        # Extract src URL
        src_match = re.search(r'src=["\']([^"\']+)["\']', img_tag, re.IGNORECASE)
        if not src_match:
            return img_tag
        
        src_url = src_match.group(1)
        
        # For external URLs, try to download and convert to data URI
        # Note: This is synchronous and may block briefly, but it's the simplest approach
        # For better UX, you could implement async loading with QNetworkAccessManager
        if not src_url.startswith('data:') and (src_url.startswith('http://') or src_url.startswith('https://')):
            try:
                import requests
                response = requests.get(src_url, timeout=5, stream=True)
                if response.status_code == 200:
                    # Get content type
                    content_type = response.headers.get('Content-Type', 'image/png')
                    # Ensure it's an image type
                    if not content_type.startswith('image/'):
                        content_type = 'image/png'
                    # Read image data
                    image_data = response.content
                    # Convert to base64
                    base64_data = base64.b64encode(image_data).decode('utf-8')
                    # Create data URI
                    data_uri = f'data:{content_type};base64,{base64_data}'
                    # Replace src in img tag
                    img_tag = re.sub(
                        r'src=["\'][^"\']+["\']',
                        f'src="{data_uri}"',
                        img_tag,
                        flags=re.IGNORECASE
                    )
            except Exception:
                # If download fails, keep original tag (image won't load)
                # External images without download won't display in QTextEdit
                pass
        
        # Ensure images have proper sizing to prevent layout issues
        if 'style=' not in img_tag or 'max-width' not in img_tag:
            # Add style if not present, or append to existing style
            if 'style=' in img_tag:
                img_tag = re.sub(
                    r'style="([^"]*)"',
                    r'style="\1; max-width: 100%; height: auto;"',
                    img_tag,
                    flags=re.IGNORECASE
                )
            else:
                img_tag = img_tag.replace('<img', '<img style="max-width: 100%; height: auto;"')
        
        return img_tag
    
    return re.sub(r'<img[^>]*>', process_image_tag, html_content, flags=re.IGNORECASE)


def strip_images_for_debug(html_content: str) -> str:
    """Return a copy of *html_content* with embedded image data removed.

    Base64 data URIs can be megabytes long, making the HTML impractical to
    read, copy, or diff.  This replaces every ``src="data:..."`` value with a
    short placeholder so the surrounding markup — including any inline styles
    and colour specifications — remains intact and inspectable.

    External ``src`` URLs (http/https) are left untouched.
    """
    return re.sub(
        r'src=["\']data:[^"\']*["\']',
        'src="[image data stripped]"',
        html_content,
        flags=re.IGNORECASE,
    )


def convert_plain_text_to_html(text: str) -> str:
    """Convert plain text to HTML for display
    
    Args:
        text: Plain text content
        
    Returns:
        HTML formatted text
    """
    escaped_body = html.escape(text)
    # Preserve newlines
    html_body = escaped_body.replace('\n', '<br>')
    # Wrap in a simple HTML structure for better styling
    html_body = f'<div style="font-family: Arial, sans-serif; line-height: 1.5;">{html_body}</div>'
    return html_body


def is_html_content(content: str) -> bool:
    """Check if content appears to be HTML
    
    Args:
        content: Content to check
        
    Returns:
        True if content contains HTML tags
    """
    html_pattern = re.compile(r'<[a-z][\s\S]*>', re.IGNORECASE)
    return bool(html_pattern.search(content))
