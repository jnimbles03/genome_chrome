# app/api/dashboard.py
# API endpoints for the dashboard

from flask import Blueprint, jsonify, request, g
from app.services.storage import get_db, save_form, create_client_batch, OpenBatchLimitExceeded
from app.api.auth import require_client
from app.api.batch import open_batch_limit_response, too_many_urls_response
from app.core.config import Config
import json
import logging
import time

logger = logging.getLogger(__name__)

dashboard_bp = Blueprint('dashboard', __name__, url_prefix='/api')

@dashboard_bp.route('/records_unique', methods=['GET'])
@dashboard_bp.route('/team/forms', methods=['GET'])
def get_team_forms():
    """
    Get all analyzed forms for the team dashboard.
    Returns a list of form objects with comprehensive metadata.
    """
    try:
        limit = request.args.get('limit', 1000, type=int)
        
        with get_db() as conn:
            cursor = conn.cursor()
            
            # Fetch all completed forms
            cursor.execute("""
                SELECT 
                    id, title, entity_name, complexity, metadata, url, url_hash,
                    analyzed_at, created_at, status
                FROM forms 
                WHERE status = 'completed'
                ORDER BY analyzed_at DESC
                LIMIT %s
            """, (limit,))
            
            rows = cursor.fetchall()
            
            results = []
            for row in rows:
                meta = {}
                if row['metadata']:
                    try:
                        if isinstance(row['metadata'], dict):
                            meta = row['metadata']
                        else:
                            meta = json.loads(row['metadata'])
                    except:
                        pass
                
                # Determine LLM Model
                is_gemini = meta.get('gemini_analyzed', True)
                llm_model = "Gemini 2.5 Flash" if is_gemini else "Claude 3 Opus"
                
                # Construct record
                record = {
                    "analysis_id": str(row['id']),
                    "user_id": meta.get('user_id', 'anonymous'), # Default to anonymous if not present
                    
                    # Core Fields
                    "form_name": row['title'] or meta.get('title') or "Untitled",
                    "entity_name": row['entity_name'] or "Unknown",
                    "source_url": row['url'],
                    "complexity_score": row['complexity'],
                    "nigo_score": meta.get('nigo_score', 0),
                    "confidence_tier": meta.get('confidence_tier', 'High'),
                    "action_type": meta.get('action_type', ''),
                    "llm_model": llm_model,
                    
                    # Flags & Counts
                    "pages": meta.get('pages', 1),
                    "total_field_count": meta.get('total_field_count', 0),
                    
                    "signature_required": bool(meta.get('signature_required', False)),
                    "signature_count": meta.get('signature_analysis', {}).get('signature_count') or meta.get('signature_count', 0),
                    
                    "notarization_required": bool(meta.get('notarization_required', False)),
                    
                    "attachments_required": bool(meta.get('attachments_required', False)),
                    "attachment_count": meta.get('attachment_count', 0),
                    
                    "payment_required": bool(meta.get('payment_required', False)),
                    "payment_amount": meta.get('payment_amount', ''),
                    
                    "identification_required": bool(meta.get('identification_required', False)),
                    "conditional_logic": bool(meta.get('conditional_logic', False)),
                    "third_party_involved": bool(meta.get('third_party_involved', False)),
                    "witnesses_required": bool(meta.get('witnesses_required', False)),
                    "deadlines_present": bool(meta.get('deadlines_present', False)),
                    
                    # Classification
                    "form_purpose": meta.get('form_purpose', ''),
                    "industry_vertical": meta.get('industry_vertical', ''),
                    "industry_subvertical": meta.get('industry_subvertical', ''),
                    
                    # Estimates
                    "estimated_signer_time": meta.get('estimated_signer_time', ''),
                    "estimated_processing_time": meta.get('estimated_processing_time', ''),
                    
                    # Timestamps
                    "analyzed_at": row['analyzed_at'] * 1000 if row['analyzed_at'] else None, # JS expects ms
                }
                results.append(record)
                
            return jsonify({
                "ok": True,
                "forms": results,
                "count": len(results)
            })

    except Exception as e:
        logger.error(f"Failed to get team forms: {str(e)}")
        return jsonify({"ok": False, "error": str(e)}), 500

@dashboard_bp.route('/team/sync', methods=['POST'])
def sync_team_form():
    """
    Sync a form analysis result from the Chrome Extension.
    Upserts the form record in the database.
    """
    try:
        data = request.json
        form_data = data.get('form_data')
        user_id = data.get('user_id')
        
        if not form_data:
            return jsonify({"ok": False, "error": "Missing form_data"}), 400

        # Extract core fields
        url = form_data.get('source_url')
        if not url:
            # Fallback for local files or missing URLs
            import uuid
            url = f"unknown://{uuid.uuid4()}"
            logger.warning(f"Sync received form with missing source_url. Generated: {url}")
            
        title = form_data.get('form_name', 'Untitled')
        entity_name = form_data.get('entity_name', 'Unknown')
        complexity = form_data.get('complexity_score', 0)
        
        # Prepare metadata (everything else)
        metadata = form_data.copy()
        metadata['user_id'] = user_id
        
        # Save to DB (Upsert)
        save_form(url, title, entity_name, complexity, metadata)
        
        return jsonify({"ok": True, "message": "Form synced successfully"})

    except Exception as e:
        logger.error(f"Failed to sync form: {str(e)}")
        return jsonify({"ok": False, "error": str(e)}), 500


@dashboard_bp.route('/team/update', methods=['POST'])
def update_team_form():
    """
    Update a form record directly from the dashboard.
    """
    try:
        data = request.json
        analysis_id = data.get('analysis_id')
        form_data = data.get('form_data')
        
        if not analysis_id or not form_data:
            return jsonify({"ok": False, "error": "Missing analysis_id or form_data"}), 400

        with get_db() as conn:
            cursor = conn.cursor()
            
            # Fetch existing metadata first
            cursor.execute("SELECT metadata FROM forms WHERE id = %s", (analysis_id,))
            row = cursor.fetchone()
            if not row:
                return jsonify({"ok": False, "error": "Form not found"}), 404
                
            current_meta = {}
            if row['metadata']:
                try:
                    current_meta = json.loads(row['metadata']) if isinstance(row['metadata'], str) else row['metadata']
                except:
                    pass
            
            # Update specific top-level columns
            if 'form_name' in form_data:
                cursor.execute("UPDATE forms SET title = %s WHERE id = %s", (form_data['form_name'], analysis_id))
            if 'entity_name' in form_data:
                cursor.execute("UPDATE forms SET entity_name = %s WHERE id = %s", (form_data['entity_name'], analysis_id))
            if 'complexity_score' in form_data:
                cursor.execute("UPDATE forms SET complexity = %s WHERE id = %s", (form_data['complexity_score'], analysis_id))
                
            # Update metadata fields
            # We map the flat form_data back to the metadata structure
            meta_updates = {
                "title": form_data.get('form_name'), # redundant but safe
                "total_field_count": form_data.get('total_field_count'),
                "nigo_score": form_data.get('nigo_score'),
                "action_type": form_data.get('action_type'),
                "signature_required": form_data.get('signature_required'),
                "signature_count": form_data.get('signature_count'),
                "notarization_required": form_data.get('notarization_required'),
                "attachments_required": form_data.get('attachments_required'),
                "attachment_count": form_data.get('attachment_count'),
                "payment_required": form_data.get('payment_required'),
                "payment_amount": form_data.get('payment_amount'),
                "identification_required": form_data.get('identification_required'),
                "conditional_logic": form_data.get('conditional_logic'),
                "third_party_involved": form_data.get('third_party_involved'),
                "witnesses_required": form_data.get('witnesses_required'),
                "deadlines_present": form_data.get('deadlines_present'),
                "form_purpose": form_data.get('form_purpose'),
                "industry_vertical": form_data.get('industry_vertical'),
                "industry_subvertical": form_data.get('industry_subvertical'),
                "estimated_signer_time": form_data.get('estimated_signer_time'),
                "estimated_processing_time": form_data.get('estimated_processing_time'),
                 # No way to update llm_model currently via UI, which is fine
            }
            
            # Merge updates into current_meta (only non-None values)
            for k, v in meta_updates.items():
                if v is not None:
                    current_meta[k] = v
            
            # Save updated metadata
            cursor.execute("UPDATE forms SET metadata = %s, updated_at = %s WHERE id = %s", 
                          (json.dumps(current_meta), int(time.time()), analysis_id))
            
            conn.commit()
            
            return jsonify({"ok": True, "message": "Form updated"})

    except Exception as e:
        logger.error(f"Failed to update form: {str(e)}")
        return jsonify({"ok": False, "error": str(e)}), 500


@dashboard_bp.route('/team/delete', methods=['POST'])
def delete_team_forms():
    """Delete forms by ID"""
    try:
        data = request.json
        analysis_ids = data.get('analysis_ids', [])
        
        if not analysis_ids:
            return jsonify({"ok": False, "error": "No IDs provided"}), 400
            
        # Convert to integers
        ids = [int(id) for id in analysis_ids]
        
        from app.services.storage import delete_forms
        count = delete_forms(ids)
        
        return jsonify({"ok": True, "deleted_count": count})
        
    except Exception as e:
        logger.error(f"Failed to delete forms: {str(e)}")
        return jsonify({"ok": False, "error": str(e)}), 500


@dashboard_bp.route('/team/reanalyze', methods=['POST'])
@require_client
def reanalyze_team_forms():
    """Re-analyze forms by ID"""
    try:
        data = request.json
        analysis_ids = data.get('analysis_ids', [])
        
        if not analysis_ids:
            return jsonify({"ok": False, "error": "No IDs provided"}), 400

        if len(analysis_ids) > Config.MAX_BATCH_URLS:
            return too_many_urls_response("ok")
            
        ids = [int(id) for id in analysis_ids]
        
        with get_db() as conn:
            cursor = conn.cursor()
            format_strings = ','.join(['%s'] * len(ids))
            cursor.execute(f"SELECT url FROM forms WHERE id IN ({format_strings})", tuple(ids))
            rows = cursor.fetchall()
            
        urls = [row['url'] for row in rows]
        
        if not urls:
             return jsonify({"ok": False, "error": "No valid forms found for re-analysis"}), 404

        try:
            # Priority 10 for immediate re-analysis
            batch_id, queued_count = create_client_batch(
                g.client_id,
                urls,
                Config.MAX_OPEN_BATCHES_PER_CLIENT,
                batch_name="Dashboard re-analysis",
                priority=10
            )
        except OpenBatchLimitExceeded:
            return open_batch_limit_response("ok")
            
        return jsonify({"ok": True, "queued_count": queued_count, "batch_id": batch_id})
        
    except Exception as e:
        logger.error(f"Failed to re-analyze forms: {str(e)}")
        return jsonify({"ok": False, "error": str(e)}), 500

def register_dashboard_api(app):
    """
    Register dashboard API blueprint with Flask app.
    """
    app.register_blueprint(dashboard_bp)
    logger.info("Dashboard API registered")
