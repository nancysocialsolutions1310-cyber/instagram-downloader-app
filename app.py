import instaloader
from flask import Flask, request, jsonify, render_template, send_file, make_response, Response, stream_with_context
import re
import os
import time
import requests
from io import BytesIO 
import json
import zipstream # Required for streaming ZIP files
from werkzeug.datastructures import Headers

# --- Flask & Instaloader Setup ---
app = Flask(__name__)
L = instaloader.Instaloader()

# --- INSTALOADER AUTHENTICATION FIX ---
# Load credentials securely from environment variables (Render console)
IG_USERNAME = os.environ.get('IG_USERNAME')
IG_PASSWORD = os.environ.get('IG_PASSWORD')

# Attempt to log in or load session on startup
if IG_USERNAME and IG_PASSWORD:
    try:
        # 1. Try loading a previously saved session file (preferred)
        L.load_session_from_file(IG_USERNAME)
        print(f"Instaloader: Session loaded successfully for {IG_USERNAME}.")
    except FileNotFoundError:
        # 2. If no session file, attempt login using credentials
        try:
            L.login(IG_USERNAME, IG_PASSWORD)
            # Save the session for future restarts
            L.save_session_to_file(IG_USERNAME)
            print(f"Instaloader: Login successful for {IG_USERNAME} and session saved.")
        except Exception as e:
            # This handles two-factor authentication or login failures
            print(f"Instaloader Warning: Failed to log in with credentials. Error: {e}. Falling back to anonymous access.")
    except Exception as e:
        print(f"Instaloader Warning: Error loading session file. Falling back to anonymous access. Error: {e}")
else:
    print("Instaloader Warning: No IG_USERNAME or IG_PASSWORD provided. Using anonymous access (HIGH RISK OF RATE LIMIT).")


# --- Core Scraping Logic ---
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
        
        # Determine media list for proxy service
        media_list = []
        if post.is_sidecar:
            for i, node in enumerate(post.sidecar_nodes):
                is_video = node.is_video
                file_url = node.video_url if is_video else node.display_url
                file_ext = ".mp4" if is_video else ".jpg"
                
                media_list.append({
                    "url": file_url,
                    "filename": f"insta_{shortcode}_part_{i+1}{file_ext}",
                    "is_video": is_video
                })
            download_url = post.url 
            media_type = f"Carousel ({len(media_list)} items)"
        else:
            is_video = post.is_video
            file_ext = ".mp4" if is_video else ".jpg"
            download_url = post.video_url if is_video else post.url
            
            media_list.append({
                "url": download_url,
                "filename": f"insta_{shortcode}{file_ext}",
                "is_video": is_video
            })
            media_type = "Video (Reel/Post)" if is_video else "Image Post"
        
        
        filename_base = f"instagram_{shortcode}" + (".zip" if post.is_sidecar else media_list[0]['filename'].split('_')[-1])
        
        return {
            "original_url": download_url,
            "filename": filename_base,
            "type": media_type,
            "thumbnail_url": post.url,
            "is_carousel": post.is_sidecar,
            "media_list": media_list # List of files to download (for the proxy)
        }, 200
        
    except instaloader.exceptions.InstaloaderException as e: 
        print(f"Instaloader Error: {e}") 
        # This catch handles the 401 Unauthorized status and returns the error message
        return {"error": f"Post not found, or private, or network failed. Status: {e}"}, 404
    except Exception as e:
        print(f"Unexpected Error: {e}")
        return {"error": f"An unexpected server error occurred: {str(e)}"}, 500

# --- File Streaming Functions (for ZIP) ---

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

def generate_zip_stream(media_list):
    """Creates a generator that streams a ZIP archive containing multiple files."""
    z = zipstream.ZipStream(files=True)
    
    for media_item in media_list:
        file_url = media_item['url']
        file_name = media_item['filename']
        
        try:
            # Get the content generator for the file
            content_generator = stream_file_from_url(file_url)
            
            # Add the file to the zip stream
            z.write_iter(file_name, content_generator)
        except requests.exceptions.HTTPError as e:
            # Log failure but continue zipping other files
            print(f"Failed to fetch {file_name}: {e}. Skipping.")
        
    # Yield all chunks from the zip generator
    for chunk in z:
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
    """Route to handle downloading single files or streaming ZIP archives."""
    media_list_json = request.args.get('media_list')
    filename = request.args.get('filename')

    if not media_list_json or not filename:
        return "Missing file parameters.", 400
    
    try:
        media_list = json.loads(media_list_json)
    except json.JSONDecodeError:
        return "Invalid media list format.", 400
    
    # Determine if we are serving a single file or a ZIP
    if len(media_list) > 1 or filename.endswith('.zip'):
        # --- ZIP ARCHIVE STREAMING ---
        if not filename.endswith('.zip'):
             filename = filename + '.zip'
             
        headers = Headers()
        headers['Content-Type'] = 'application/zip'
        headers['Content-Disposition'] = f'attachment; filename="{filename}"'

        return Response(stream_with_context(generate_zip_stream(media_list)), headers=headers)

    else:
        # --- SINGLE FILE STREAMING ---
        item = media_list[0]
        url = item['url']
        
        try:
            mimetype = 'video/mp4' if item['is_video'] else 'image/jpeg'
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
