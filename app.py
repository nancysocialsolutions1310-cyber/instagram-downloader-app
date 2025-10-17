import instaloader
from flask import Flask, request, jsonify, render_template, send_file, make_response
import re
import os
import time
import requests
from io import BytesIO

# --- Flask & Instaloader Setup ---
app = Flask(__name__)
L = instaloader.Instaloader()

# --- Core Scraping Logic ---
def get_media_details(instagram_url):
    """Fetches the direct media URL and filename from an Instagram Post URL."""
    match = re.search(r'(?:/p/|/reel/|/tv/)([^/]+)', instagram_url)
    if not match:
        return {"error": "Invalid Instagram URL format."}, 400
        
    shortcode = match.group(1)
    time.sleep(1.5) 

    try:
        post = instaloader.Post.from_shortcode(L.context, shortcode)
        
        download_url = None
        media_type = "Image"
        
        # We MUST use post.url for the thumbnail preview and general metadata
        thumbnail_url = post.url 

        if post.is_video:
            download_url = post.video_url
            media_type = "Video (Reel/Post)"
        elif post.is_image:
            download_url = post.url
            media_type = "Image Post"
        elif post.is_sidecar and post.sidecar_nodes:
            # For carousels, grab the first media item
            first_node = post.sidecar_nodes[0]
            download_url = first_node.video_url if first_node.is_video else first_node.display_url
            media_type = f"Carousel ({'Video' if first_node.is_video else 'Image'})"
        else:
            return {"error": "Unsupported media type or post restriction detected."}, 404

        extension = ".mp4" if download_url and (download_url.endswith(".mp4") or post.is_video) else ".jpg"
        filename = f"instagram_{shortcode}{extension}"

        return {
            # CRITICAL CHANGE: Return the Instagram CDN URL AND the filename
            "original_url": download_url, 
            "filename": filename,
            "type": media_type,
            "thumbnail_url": thumbnail_url
        }, 200
        
    except instaloader.exceptions.InstaloaderException as e: 
        print(f"Instaloader Error: {e}") 
        return {"error": "Post not found, or private, or network failed."}, 404
    except Exception as e:
        print(f"Unexpected Error: {e}")
        return {"error": f"An unexpected server error occurred: {str(e)}"}, 500

# --- Flask Routes ---

@app.route('/api/download', methods=['POST'])
def download_api():
    """API endpoint to handle scraping and return data for the frontend."""
    data = request.get_json()
    instagram_url = data.get('url')
    
    if not instagram_url:
        return jsonify({"error": "Missing 'url' in request body."}), 400

    result, status_code = get_media_details(instagram_url)
    return jsonify(result), status_code

@app.route('/download_proxy', methods=['GET'])
def download_proxy():
    """
    NEW ROUTE: Downloads the file to the server and serves it to the user.
    This bypasses cross-origin and security blocks.
    """
    file_url = request.args.get('url')
    filename = request.args.get('filename')
    
    if not file_url or not filename:
        return make_response("Missing file URL or filename.", 400)

    # Use a standard User-Agent header to avoid being blocked by the CDN
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/108.0.0.0 Safari/537.36'
    }

    try:
        # Use requests to get the content from Instagram's CDN
        r = requests.get(file_url, headers=headers, stream=True, timeout=10) 
        r.raise_for_status() # Raise exception for bad status codes (4xx or 5xx)

        # Use BytesIO to stream the content directly without saving a temporary file to disk
        file_data = BytesIO(r.content)

        # Force the browser to download the file using send_file
        return send_file(
            file_data,
            mimetype=r.headers.get('Content-Type', 'application/octet-stream'),
            as_attachment=True,
            download_name=filename 
        )

    except requests.exceptions.RequestException as e:
        print(f"Proxy Download Error: {e}")
        return make_response(f"Failed to fetch media from Instagram's CDN. (Error: {e})", 503)
    except Exception as e:
        return make_response(f"An unknown proxy error occurred: {str(e)}", 500)


@app.route('/', methods=['GET'])
def index():
    return render_template('index.html')

if __name__ == '__main__':
    app.run(debug=True, host='127.0.0.1', port=5000)
