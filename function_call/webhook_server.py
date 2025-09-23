from flask import Flask, request, jsonify, render_template, send_from_directory
from flask_cors import CORS
import json
import os
import sys
from datetime import datetime

# Add the current directory to Python path to import our modules
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

try:
    from voice_agent_function import function_call
    print("✅ Successfully imported voice_agent_function")
except ImportError as e:
    print(f"❌ Error importing voice_agent_function: {e}")
    print("Make sure voice_agent_function.py is in the same directory")
    sys.exit(1)

# Initialize Flask app
app = Flask(__name__, 
           static_folder='static',
           template_folder='templates')
CORS(app)  # Enable CORS for frontend integration

# Configure JSON response settings
app.config['JSON_SORT_KEYS'] = False
app.config['JSONIFY_PRETTYPRINT_REGULAR'] = True

# Store search history for analytics
search_history = []

@app.route('/')
def index():
    """Serve the main frontend page"""
    return render_template('product_search.html')

@app.route('/search_products', methods=['POST'])
def search_products():
    """
    Main webhook endpoint for product search
    Expected payload: {"query": "user search query"}
    """
    try:
        # Get request data
        if request.is_json:
            data = request.get_json()
        else:
            return jsonify({
                "success": False,
                "error": "Invalid request format. Expected JSON."
            }), 400

        # Extract query
        user_query = data.get('query', '').strip()
        if not user_query:
            return jsonify({
                "success": False,
                "error": "Query parameter is required and cannot be empty"
            }), 400

        # Log the search
        search_entry = {
            "timestamp": datetime.now().isoformat(),
            "query": user_query,
            "ip": request.remote_addr,
            "user_agent": request.headers.get('User-Agent', '')
        }
        search_history.append(search_entry)
        
        print(f"🔍 Processing search: '{user_query}' from {request.remote_addr}")

        # Call our voice agent function
        result = function_call(user_query)
        
        # Add metadata to response
        result['timestamp'] = search_entry['timestamp']
        result['processing_time'] = "< 1s"  # You can add actual timing if needed
        
        # Log result
        if result.get('success'):
            match_type = result.get('match_type', 'unknown')
            total_results = len(result.get('results', {}).get('primary_matches', []))
            print(f"✅ Search completed: {match_type}, {total_results} results")
        else:
            print(f"❌ Search failed: {result.get('error', 'Unknown error')}")

        return jsonify(result), 200 if result.get('success') else 404

    except Exception as e:
        error_response = {
            "success": False,
            "error": f"Server error: {str(e)}",
            "query": user_query if 'user_query' in locals() else None
        }
        print(f"💥 Server error: {e}")
        return jsonify(error_response), 500

@app.route('/search_products', methods=['GET'])
def search_products_get():
    """GET endpoint for simple URL-based searches"""
    query = request.args.get('q', '').strip()
    if not query:
        return jsonify({
            "success": False,
            "error": "Query parameter 'q' is required"
        }), 400
    
    # Reuse POST logic
    return search_products_post_logic(query)

def search_products_post_logic(query):
    """Shared logic for POST and GET searches"""
    try:
        result = function_call(query)
        result['timestamp'] = datetime.now().isoformat()
        return jsonify(result), 200 if result.get('success') else 404
    except Exception as e:
        return jsonify({
            "success": False,
            "error": f"Server error: {str(e)}",
            "query": query
        }), 500

@app.route('/health', methods=['GET'])
def health_check():
    """Health check endpoint"""
    return jsonify({
        "status": "healthy",
        "service": "Voice Agent Product Search",
        "timestamp": datetime.now().isoformat(),
        "total_searches": len(search_history)
    })

@app.route('/analytics', methods=['GET'])
def analytics():
    """Basic analytics endpoint"""
    if not search_history:
        return jsonify({
            "total_searches": 0,
            "recent_searches": []
        })
    
    # Get recent searches (last 20)
    recent = search_history[-20:]
    
    # Basic stats
    total_searches = len(search_history)
    unique_queries = len(set(entry['query'].lower() for entry in search_history))
    
    return jsonify({
        "total_searches": total_searches,
        "unique_queries": unique_queries,
        "recent_searches": [
            {
                "query": entry['query'],
                "timestamp": entry['timestamp']
            } for entry in recent
        ]
    })

@app.route('/test', methods=['GET'])
def test_endpoint():
    """Test endpoint with sample queries"""
    sample_queries = [
        "iPhone 15 Pro",
        "gaming laptop under $1500", 
        "wireless headphones under $200",
        "Samsung Galaxy S24"
    ]
    
    return render_template('test_page.html', sample_queries=sample_queries)

@app.errorhandler(404)
def not_found(error):
    return jsonify({
        "success": False,
        "error": "Endpoint not found",
        "available_endpoints": [
            "/search_products (POST/GET)",
            "/health (GET)",
            "/analytics (GET)",
            "/test (GET)",
            "/ (GET - Frontend)"
        ]
    }), 404

@app.errorhandler(500)
def internal_error(error):
    return jsonify({
        "success": False,
        "error": "Internal server error"
    }), 500

# Development helper routes
@app.route('/debug/search/<query>')
def debug_search(query):
    """Debug endpoint for quick testing"""
    try:
        result = function_call(query)
        return f"""
        <h2>Debug Search: "{query}"</h2>
        <pre style="background: #f5f5f5; padding: 15px; border-radius: 5px;">
{json.dumps(result, indent=2)}
        </pre>
        <br>
        <a href="/">← Back to Search</a>
        """
    except Exception as e:
        return f"<h2>Error:</h2><p>{str(e)}</p><a href='/'>← Back</a>"

if __name__ == '__main__':
    # Create templates directory if it doesn't exist
    os.makedirs('templates', exist_ok=True)
    os.makedirs('static', exist_ok=True)
    
    print("🚀 Starting Voice Agent Product Search Webhook Server")
    print("=" * 60)
    print("📡 API Endpoints:")
    print("  • POST /search_products - Main search endpoint")
    print("  • GET  /search_products?q=query - URL search")  
    print("  • GET  /health - Health check")
    print("  • GET  /analytics - Search analytics")
    print("  • GET  / - Frontend interface")
    print("  • GET  /test - Test page")
    print("=" * 60)
    
    # Check if voice_agent_function is working
    try:
        test_result = function_call("test query")
        if test_result.get('success') is not None:
            print("✅ Voice agent function is working")
        else:
            print("⚠️ Voice agent function returned unexpected result")
    except Exception as e:
        print(f"❌ Voice agent function test failed: {e}")
    
    # Start server
    print("🌐 Server starting on http://localhost:5000")
    print("🔍 Try: curl -X POST http://localhost:5000/search_products -H 'Content-Type: application/json' -d '{\"query\":\"iPhone 15 Pro\"}'")
    print("=" * 60)
    
    app.run(
        host='0.0.0.0',  # Allow external connections
        port=5000,
        debug=True,      # Enable debug mode for development
        threaded=True    # Handle multiple requests
    )