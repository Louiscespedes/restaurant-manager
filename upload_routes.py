"""
Photo upload endpoints — uses Cloudinary for image storage.
Supports recipe, dish, and menu photos.
"""
import os
import json
import cloudinary
import cloudinary.uploader
from flask import Blueprint, jsonify, request
from models import Session, Recipe, Dish, Menu

upload_bp = Blueprint('upload', __name__)

# ── Cloudinary config (set these in Railway env vars) ─────────────────
cloudinary.config(
    cloud_name=os.environ.get('CLOUDINARY_CLOUD_NAME'),
    api_key=os.environ.get('CLOUDINARY_API_KEY'),
    api_secret=os.environ.get('CLOUDINARY_API_SECRET'),
    secure=True
)


@upload_bp.route('/api/upload/photo', methods=['POST'])
def upload_photo():
    """
    Upload a photo to Cloudinary.
    Accepts multipart form data with:
      - file: the image file
      - type: 'recipe', 'dish', or 'menu'
      - id: the recipe/dish/menu ID to attach the photo to (optional)
    Returns the Cloudinary URL.
    """
    if 'file' not in request.files:
        return jsonify({'error': 'No file provided'}), 400

    file = request.files['file']
    if file.filename == '':
        return jsonify({'error': 'No file selected'}), 400

    item_type = request.form.get('type', 'general')  # recipe, dish, menu
    item_id = request.form.get('id')

    try:
        # Upload to Cloudinary with auto-optimization
        result = cloudinary.uploader.upload(
            file,
            folder=f'mep/{item_type}s',
            resource_type='image',
            transformation=[
                {'quality': 'auto', 'fetch_format': 'auto'},
                {'width': 1200, 'crop': 'limit'}  # Max 1200px wide
            ]
        )

        photo_url = result['secure_url']

        # If an item ID was provided, attach the photo to it
        if item_id:
            db = Session()
            try:
                model_map = {
                    'recipe': Recipe,
                    'dish': Dish,
                    'menu': Menu,
                }
                Model = model_map.get(item_type)
                if Model:
                    item = db.query(Model).get(int(item_id))
                    if item:
                        # Parse existing photos, add new one
                        existing = json.loads(item.photos) if item.photos else []
                        existing.append(photo_url)
                        item.photos = json.dumps(existing)
                        db.commit()
            finally:
                db.close()

        return jsonify({
            'url': photo_url,
            'public_id': result.get('public_id'),
            'width': result.get('width'),
            'height': result.get('height'),
            'format': result.get('format'),
            'bytes': result.get('bytes'),
        }), 201

    except Exception as e:
        return jsonify({'error': f'Upload failed: {str(e)}'}), 500


@upload_bp.route('/api/upload/photo/delete', methods=['POST'])
def delete_photo():
    """
    Remove a photo from Cloudinary and from the item's photos list.
    Body: { "url": "...", "type": "recipe", "id": 5 }
    """
    data = request.json
    photo_url = data.get('url')
    item_type = data.get('type')
    item_id = data.get('id')

    if not photo_url:
        return jsonify({'error': 'No URL provided'}), 400

    try:
        # Extract public_id from URL for Cloudinary deletion
        # URL format: https://res.cloudinary.com/{cloud}/image/upload/v123/mep/recipes/abc123.jpg
        parts = photo_url.split('/upload/')
        if len(parts) > 1:
            public_id = parts[1].rsplit('.', 1)[0]  # Remove extension
            # Remove version prefix if present (v1234567890/)
            if public_id.startswith('v') and '/' in public_id:
                public_id = public_id.split('/', 1)[1]
            cloudinary.uploader.destroy(public_id)

        # Remove from item's photo list
        if item_type and item_id:
            db = Session()
            try:
                model_map = {
                    'recipe': Recipe,
                    'dish': Dish,
                    'menu': Menu,
                }
                Model = model_map.get(item_type)
                if Model:
                    item = db.query(Model).get(int(item_id))
                    if item and item.photos:
                        photos = json.loads(item.photos)
                        photos = [p for p in photos if p != photo_url]
                        item.photos = json.dumps(photos)
                        db.commit()
            finally:
                db.close()

        return jsonify({'status': 'deleted'})

    except Exception as e:
        return jsonify({'error': f'Delete failed: {str(e)}'}), 500
