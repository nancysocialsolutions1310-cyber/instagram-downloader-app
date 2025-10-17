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

# --- Instaloader runs in ANONYMOUS mode (Highest compatibility) ---

# --- Core Scraping Logic (Corrected for Anonymous Access) ---
def get_media_details(instagram_url, preferred_type='Reels'): 
    """Fetches the direct media URL and filename from an Instagram Post URL."""
    # 1. Extract Post Shortcode 
    match = re.search(r'(?:/p/|/reel/|/tv/)([^/]+)', instagram_url)
    if not match:
        return {"error": "Invalid Instagram URL format."}, 400
        
    shortcode = match.group(1)
    time.sleep(1.5) 

    try:
        # 3. Get the Post object (runs anonymously)
        post = instaloader.Post.from_shortcode(L.context, shortcode)
        
        target_node = post
        media_type = "Video (Reel/Post)" if post.is_video else "Image Post"
        is_carousel = False 

        # FIX: Use the sidecar_nodes list count to check for a carousel (more stable)
        if hasattr(post, 'sidecar_nodes') and len(post.sidecar_nodes) > 1:
            is_carousel = True
            sidecar_nodes = post.sidecar_nodes
            target_node = sidecar_nodes[0] # Default to first item
            media_type = f"Carousel (Item 1 of {len(sidecar_nodes)})"

            # ADVANCED LOGIC: If 'Photo' is preferred, try to find the first image
            if preferred_type == 'Photo':
                found_image = next((node for node in sidecar_nodes if not node.is_video), None)
                if found_image:
                    target_node = found_image
                    media_type = f"Carousel (First Image)"
            
            # ADVANCED LOGIC: If 'Video' or 'Reels' is preferred, try to find the first video
            elif preferred_type == 'Video' or preferred_type == 'Reels':
                found_video = next((node for node in sidecar_nodes if node.is_video), None)
                if found_video:
                    target_node = found_video
                    media_type = f"Carousel (First Video)"
        
        # --- End Logic ---
        
        is_video = target_node.is_video
        file_ext = ".mp4" if is_video else ".jpg"
        
        # Use the correct URL properties based on the target node
        download_url = target_node.video_url if is_video else target_node.display_url
        thumbnail_url = target_node.display_url
        
        # Create a simple list with only the selected item
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
            "is_carousel": is_carousel, # Return the boolean result
            "media_list": media_list 
        }, 200
        
    except instaloader.exceptions.InstaloaderException as e: 
        # This will now catch the 401 error and show a simpler message
        print(f"Instaloader Error: {e}") 
        return {"error": f"Post not found, or private, or network failed. Status: Connection Blocked."}, 404
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
    # Safely get the preferred type from the frontend
    preferred_type = data.get('preferred_type', 'Reels') 
    
    if not instagram_url:
        return jsonify({"error": "Missing 'url' in request body."}), 400

    # Pass the preference to the scraping logic
    result, status_code = get_media_details(instagram_url, preferred_type) 
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
