import instaloader
from flask import Flask, request, jsonify, render_template, send_file, make_response, Response, stream_with_context
import re
import os
import time
import requests
from io import BytesIO 
import json
from werkzeug.datastructures import Headers

# --- Flask & Instaloader Setup ---
app = Flask(__name__)
L = instaloader.Instaloader()

# --- INSTALOADER AUTHENTICATION FIX ---
# Load credentials securely from environment variables (Render console)
IG_USERNAME = os.environ.get('IG_USERNAME')
IG_PASSWORD = os.environ.get('IG_PASSWORD')

# CRITICAL FIX: Define the expected session file path (in the same directory as app.py)
SESSION_FILE_PATH = f"{IG_USERNAME}.session"

# Attempt to log in or load session on startup
if IG_USERNAME and IG_PASSWORD:
    try:
        # 1. Try loading a previously saved session file from the project root
        L.load_session_from_file(IG_USERNAME, filename=SESSION_FILE_PATH)
        print(f"Instaloader: Session loaded successfully from project root for {IG_USERNAME}.")
    except FileNotFoundError:
        # 2. If no session file in project root, attempt login using credentials
        try:
            print(f"Instaloader: Session file not found. Attempting fresh login for {IG_USERNAME}...")
            L.login(IG_USERNAME, IG_PASSWORD)
            
            # Save the session to the PROJECT ROOT (where Render expects it)
            L.save_session_to_file(IG_USERNAME, filename=SESSION_FILE_PATH)
            print(f"Instaloader: Login successful and session saved to {SESSION_FILE_PATH}.")
        except Exception as e:
            # This handles two-factor authentication or login failures
            print(f"Instaloader Warning: Failed to log in with credentials. Error: {e}. Falling back to anonymous access.")
    except Exception as e:
        print(f"Instaloader Warning: General error during session loading. Falling back to anonymous access. Error: {e}")
else:
    print("Instaloader Warning: No IG_USERNAME or IG_PASSWORD provided. Using anonymous access (HIGH RISK OF RATE LIMIT).")


# --- Core Scraping Logic (ZIP Logic Removed) ---
def get_media_details(instagram_url):
    """Fetches the direct media URL and filename from an Instagram Post URL."""
    # 1. Extract Post Shortcode 
    match = re.search(r'(?:/p/|/reel/|/tv/)([^/]+)', instagram_url)
    if not match:
        return {"error": "Invalid Instagram URL format."}, 400
        
    shortcode = match.group(1)
    time.sleep(1.5) 

    try:
        # 3. Get the Post object (uses the global, potentially authenticated L object)
        post = instaloader.Post.from_shortcode(L.context, shortcode)
        
        # --- LOGIC MODIFIED: Get ONLY the FIRST media item details ---
        
        # Default to the post itself
        target_node = post
        media_type = "Video (Reel/Post)" if post.is_video else "Image Post"

        if post.is_sidecar:
            # For carousels, grab the first media item only
            target_node = post.sidecar_nodes[0]
            media_type = f"Carousel (Item 1 of {len(post.sidecar_nodes)})"
        
        is_video = target_node.is_video
        file_ext = ".mp4" if is_video else ".jpg"
        
        download_url = target_node.video_url if is_video else target_node.display_url
        thumbnail_url = target_node.display_url
        
        # Create a simple list with only the first item (needed for the proxy route format)
        media_list = [{
            "url": download_url,
            "filename": f"insta_{shortcode}{file_ext}",
            "is_video": is_video
        }]
        
        filename_base = f"instagram_{shortcode}{file_ext}"
        
        return {
            "original_url": download_url,
            "filename": filename_base,
            "type": media_type,
            "thumbnail_url": thumbnail_url,
            "is_carousel": post.is_sidecar,
            "media_list": media_list 
        }, 200
        
    except instaloader.exceptions.InstaloaderException as e: 
        print(f"Instaloader Error: {e}") 
        # This catch handles the 401 Unauthorized status and returns the error message
        return {"error": f"Post not found, or private, or network failed. Status: {e}"}, 404
    except Exception as e:
        print(f"Unexpected Error: {e}")
        return {"error": f"An unexpected server error occurred: {str(e)}"}, 500

# --- File Streaming Function (Single File Only) ---

def stream_file_from_url(url):
    """Streams a single file content from an external URL."""
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
    }
    # Use stream=True for large files
    response = requests.get(url, headers=headers, stream=True)
    response.raise_for_status()
    
    # Generator yields chunks of file data
    for chunk in response.iter_content(chunk_size=8192):
        yield chunk


# --- Flask Routes ---

@app.route('/api/download', methods=['POST'])
def download_api():
    """API endpoint to scrape the URL and return data for the frontend."""
    data = request.get_json()
    instagram_url = data.get('url')
    
    if not instagram_url:
        return jsonify({"error": "Missing 'url' in request body."}), 400

    result, status_code = get_media_details(instagram_url)
    return jsonify(result), status_code


@app.route('/download_proxy', methods=['GET'])
def download_proxy():
    """Route to handle downloading single files."""
    media_list_json = request.args.get('media_list')
    filename = request.args.get('filename')

    if not media_list_json or not filename:
        return "Missing file parameters.", 400
    
    try:
        media_list = json.loads(media_list_json)
    except json.JSONDecodeError:
        return "Invalid media list format.", 400
    
    # Since we removed ZIP logic, we only stream the first item
    if not media_list:
        return "No media found to stream.", 404

    # --- SINGLE FILE STREAMING ---
    item = media_list[0]
    url = item['url']
    
    try:
        mimetype = 'video/mp4' if item['is_video'] else 'image/jpeg'
        
        # Use stream_with_context to send file efficiently
        return Response(stream_with_context(stream_file_from_url(url)),
                        headers={'Content-Disposition': f'attachment; filename="{filename}"',
                                 'Content-Type': mimetype})
        
    except requests.exceptions.RequestException as e:
        print(f"Single file proxy download failed: {e}")
        return "Failed to retrieve single file from source.", 503


@app.route('/', methods=['GET'])
def index():
    return render_template('index.html')


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
