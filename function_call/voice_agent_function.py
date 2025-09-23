import warnings
warnings.filterwarnings('ignore', category=DeprecationWarning)
import os
os.environ['TF_CPP_MIN_LOG_LEVEL'] = '2'
os.environ['TF_ENABLE_ONEDNN_OPTS'] = '0'

import re
import json
import numpy as np
from typing import Dict, List, Optional, Tuple
from dotenv import load_dotenv
import google.generativeai as genai
from rag_db_tool import PRODUCT_DATABASE, embed_text, load_index_and_mapping

# -------------------------
# Setup
# -------------------------
load_dotenv()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY_1")
genai.configure(api_key=GEMINI_API_KEY)

# Load FAISS index
try:
    index, id_to_product = load_index_and_mapping()
    print("✅ Database loaded successfully")
except Exception as e:
    print(f"❌ Error loading database: {e}")
    index, id_to_product = None, {}

# -------------------------
# Enhanced Helper Functions
# -------------------------
def extract_budget_from_query(query: str) -> Optional[Tuple[float, float]]:
    """Extract budget from user query with better parsing"""
    query_lower = query.lower().strip()
    
    # Remove common words that might interfere
    query_lower = re.sub(r'\b(want|need|looking|for|a|an|the)\b', ' ', query_lower)
    
    # Under/below patterns
    under_match = re.search(r'under\s*\$?(\d+)', query_lower)
    below_match = re.search(r'below\s*\$?(\d+)', query_lower)
    if under_match or below_match:
        amount = float(under_match.group(1) if under_match else below_match.group(1))
        return (0, amount)
    
    # Above/over patterns
    above_match = re.search(r'above\s*\$?(\d+)', query_lower)
    over_match = re.search(r'over\s*\$?(\d+)', query_lower)
    if above_match or over_match:
        amount = float(above_match.group(1) if above_match else over_match.group(1))
        return (amount, 10000)
    
    # Range patterns: $100-300, $100 to $300, between $100 and $300
    range_patterns = [
        r'\$?(\d+)\s*[-–—to]\s*\$?(\d+)',
        r'between\s*\$?(\d+)\s*and\s*\$?(\d+)',
        r'\$?(\d+)\s*-\s*\$?(\d+)'
    ]
    
    for pattern in range_patterns:
        match = re.search(pattern, query_lower)
        if match:
            min_price, max_price = float(match.group(1)), float(match.group(2))
            return (min(min_price, max_price), max(min_price, max_price))
    
    # Single price (assume max budget)
    single_price = re.search(r'\$(\d+)(?!\s*[-–—to])', query_lower)
    if single_price:
        return (0, float(single_price.group(1)))
    
    return None

def get_price_value(price_str: str) -> float:
    """Convert price string to numeric value with better error handling"""
    if not price_str:
        return 0
    try:
        # Handle different price formats: $299, $1,299.99, 299.99, etc.
        cleaned = re.sub(r'[^\d.,]', '', str(price_str))
        # Remove commas (thousands separator)
        cleaned = cleaned.replace(',', '')
        return float(cleaned) if cleaned else 0
    except (ValueError, TypeError):
        return 0

def extract_product_keywords(query: str) -> List[str]:
    """Extract meaningful product keywords from query"""
    # Remove budget-related phrases first
    cleaned_query = re.sub(r'under \$?\d+|above \$?\d+|below \$?\d+|\$\d+[-–—\d]*|between.+and.+\d+', '', query.lower())
    
    # Remove common filler words
    stop_words = {'i', 'want', 'need', 'looking', 'for', 'a', 'an', 'the', 'with', 'good', 'best', 'some'}
    
    # Extract words
    words = re.findall(r'\b[a-zA-Z]+\b', cleaned_query)
    keywords = [word for word in words if word not in stop_words and len(word) > 2]
    
    return keywords

def calculate_exact_match_score(product: Dict, query: str, keywords: List[str]) -> float:
    """Calculate if this is an exact product match with improved logic"""
    score = 0
    product_name = product.get('name', '').lower()
    product_category = product.get('category', '').lower()
    product_description = product.get('description', '').lower()
    
    # Clean query for comparison
    query_clean = re.sub(r'under \$?\d+|above \$?\d+|below \$?\d+|\$\d+[-–—\d]*', '', query.lower()).strip()
    query_clean = re.sub(r'\b(want|need|looking|for|a|an|the|i|with)\b', ' ', query_clean).strip()
    query_clean = re.sub(r'\s+', ' ', query_clean)  # Remove extra spaces
    
    # 1. Exact product name match (highest priority)
    if query_clean in product_name or product_name in query_clean:
        score += 100
    
    # 2. Check if all major keywords appear in product name
    keyword_matches_in_name = sum(1 for keyword in keywords if keyword in product_name)
    if len(keywords) >= 2 and keyword_matches_in_name >= len(keywords) * 0.8:  # 80% of keywords match
        score += 80
    
    # 3. Brand + model detection (like "iphone 15", "samsung galaxy")
    if len(keywords) >= 2:
        # Check consecutive keyword pairs
        for i in range(len(keywords) - 1):
            keyword_pair = f"{keywords[i]} {keywords[i+1]}"
            if keyword_pair in product_name:
                score += 60  # High score for consecutive matches
        
        # Check if first keyword is brand-like and appears in name
        potential_brands = ['apple', 'samsung', 'sony', 'asus', 'dell', 'hp', 'lenovo', 'microsoft']
        if keywords[0] in potential_brands and keywords[0] in product_name:
            score += 30
    
    # 4. Category + product type match (like "gaming laptop", "wireless headphones")
    category_product_matches = 0
    for keyword in keywords:
        if keyword in product_name:
            category_product_matches += 25
        elif keyword in product_category:
            category_product_matches += 15
        elif keyword in product_description:
            category_product_matches += 5
    
    score += category_product_matches
    
    # 5. Penalize mismatched categories (avoid cross-category false positives)
    query_implies_phone = any(word in query_clean for word in ['phone', 'smartphone', 'iphone', 'galaxy', 'pixel'])
    query_implies_laptop = any(word in query_clean for word in ['laptop', 'macbook', 'notebook'])
    query_implies_headphones = any(word in query_clean for word in ['headphones', 'earbuds', 'airpods'])
    
    product_is_phone = 'phone' in product_category or 'smartphone' in product_category
    product_is_laptop = 'laptop' in product_category or 'computer' in product_category
    product_is_audio = 'audio' in product_category or 'headphones' in product_category
    
    # Apply category mismatch penalty
    if query_implies_phone and not product_is_phone:
        score *= 0.3  # Heavy penalty
    elif query_implies_laptop and not product_is_laptop:
        score *= 0.5
    elif query_implies_headphones and not product_is_audio:
        score *= 0.5
    
    return score

def calculate_similarity_score(product: Dict, query: str, keywords: List[str]) -> float:
    """Calculate general similarity score for related products"""
    score = 0
    
    product_text = {
        'name': product.get('name', '').lower(),
        'category': product.get('category', '').lower(),
        'description': product.get('description', '').lower(),
        'features': ' '.join(product.get('features', [])).lower()
    }
    
    # Score keyword matches with different weights
    for keyword in keywords:
        if keyword in product_text['name']:
            score += 10
        elif keyword in product_text['category']:
            score += 8
        elif keyword in product_text['features']:
            score += 6
        elif keyword in product_text['description']:
            score += 4
    
    # Bonus for product rating
    rating = product.get('rating', 0)
    if isinstance(rating, (int, float)):
        score += rating * 0.8
    
    return score

def is_budget_compatible(product_price: str, budget_range: Optional[Tuple[float, float]]) -> bool:
    """Check if product fits budget"""
    if not budget_range:
        return True
    
    price = get_price_value(product_price)
    return budget_range[0] <= price <= budget_range[1]

# -------------------------
# Main Function Call Tool
# -------------------------
def function_call(user_query: str) -> Dict:
    """
    Enhanced function call tool for voice agent
    
    Logic:
    1. Parse query for budget and keywords
    2. Search database using semantic + keyword matching
    3. Determine if exact match or similar options
    4. Return structured response with products
    """
    try:
        print(f"🔍 Processing: '{user_query}'")
        
        if not user_query or not isinstance(user_query, str):
            return {
                "success": False,
                "error": "Invalid query: must be a non-empty string"
            }
        
        # Step 1: Parse query
        budget_range = extract_budget_from_query(user_query)
        keywords = extract_product_keywords(user_query)
        
        print(f"💡 Keywords: {keywords}")
        if budget_range:
            print(f"💰 Budget: ${budget_range[0]} - ${budget_range[1]}")
        
        # Step 2: Get candidate products
        candidates = []
        
        # Try semantic search first (AI-powered)
        if index is not None and id_to_product:
            try:
                query_embedding = embed_text(user_query)
                distances, indices = index.search(np.array([query_embedding]).astype("float32"), 20)
                
                for i in indices[0]:
                    if i != -1 and i in id_to_product:
                        candidates.append(id_to_product[i])
                        
                print(f"🧠 Semantic search: {len(candidates)} candidates")
                        
            except Exception as e:
                print(f"⚠️ Semantic search failed: {e}")
        
        # Fallback: use full database
        if not candidates:
            candidates = PRODUCT_DATABASE.copy()
            print(f"📚 Using full database: {len(candidates)} products")
        
        # Step 3: Apply budget filter
        if budget_range:
            budget_filtered = [p for p in candidates if is_budget_compatible(p.get('price'), budget_range)]
            if budget_filtered:
                candidates = budget_filtered
                print(f"💸 Budget filtered: {len(candidates)} products")
            else:
                print(f"⚠️ No products match budget, showing all results")
        
        # Step 4: Score products for exact vs similar matches
        exact_matches = []
        similar_matches = []
        
        for product in candidates:
            exact_score = calculate_exact_match_score(product, user_query, keywords)
            similarity_score = calculate_similarity_score(product, user_query, keywords)
            
            product_with_scores = {
                **product,
                "exact_score": exact_score,
                "similarity_score": similarity_score,
                "total_score": exact_score + similarity_score
            }
            
            # Classify as exact match if high exact score
            if exact_score >= 30:  # Lowered threshold for better detection
                exact_matches.append(product_with_scores)
            else:
                similar_matches.append(product_with_scores)
        
        # Sort by scores
        exact_matches.sort(key=lambda x: x['total_score'], reverse=True)
        similar_matches.sort(key=lambda x: x['total_score'], reverse=True)
        
        # Debug output
        print(f"🔍 Debug - Exact matches: {len(exact_matches)}, Similar: {len(similar_matches)}")
        if exact_matches:
            print(f"    Top exact: {exact_matches[0]['name']} (score: {exact_matches[0]['exact_score']:.1f})")
        if similar_matches:
            print(f"    Top similar: {similar_matches[0]['name']} (score: {similar_matches[0]['similarity_score']:.1f})")
        
        # Step 5: Determine response type and prepare results
        if exact_matches:
            # Found exact matches
            match_type = "exact_match"
            primary_products = exact_matches[:3]  # Top 3 exact matches
            message = f"Found exact match for '{user_query}'"
            
            # Add some similar options if available
            additional_products = similar_matches[:2] if similar_matches else []
            
        elif similar_matches:
            # No exact matches, but found similar products
            match_type = "similar_options" 
            primary_products = similar_matches[:5]  # Top 5 similar
            message = f"Found similar options for '{user_query}'"
            additional_products = []
            
        else:
            # No matches found
            return {
                "success": False,
                "error": f"No products found matching '{user_query}'",
                "query": user_query,
                "keywords_searched": keywords
            }
        
        # Step 6: Format response
        def format_product(product):
            return {
                "id": product.get("id"),
                "name": product.get("name"),
                "price": product.get("price"),
                "category": product.get("category"),
                "description": product.get("description", "")[:200] + "..." if len(product.get("description", "")) > 200 else product.get("description", ""),
                "features": product.get("features", [])[:5],  # Top 5 features
                "rating": product.get("rating"),
                "match_confidence": round(product.get("total_score", 0) / 10, 1)  # Normalized confidence score
            }
        
        response = {
            "success": True,
            "query": user_query,
            "keywords_searched": keywords,
            "match_type": match_type,
            "message": message,
            "budget_applied": budget_range is not None,
            "budget_range": budget_range,
            "results": {
                "primary_matches": [format_product(p) for p in primary_products],
                "additional_options": [format_product(p) for p in additional_products] if additional_products else [],
                "total_primary": len(primary_products),
                "total_additional": len(additional_products)
            }
        }
        
        print(f"✅ Result: {match_type} - {len(primary_products)} primary matches")
        return response
        
    except Exception as e:
        return {
            "success": False,
            "error": f"Function call failed: {str(e)}",
            "query": user_query
        }

# -------------------------
# Testing Functions
# -------------------------
def test_query(query: str):
    """Test a single query and display results"""
    print(f"\n🧪 Testing: '{query}'")
    print("=" * 50)
    
    result = function_call(query)
    
    if result["success"]:
        print(f"✅ {result['message']}")
        print(f"🎯 Match Type: {result['match_type']}")
        
        primary = result["results"]["primary_matches"]
        additional = result["results"]["additional_options"]
        
        print(f"\n🏆 PRIMARY MATCHES ({len(primary)}):")
        for i, product in enumerate(primary, 1):
            print(f"  {i}. {product['name']}")
            print(f"     💰 {product['price']} | ⭐ {product['rating']} | 🎯 {product['match_confidence']}")
            print(f"     📝 {product['description']}")
        
        if additional:
            print(f"\n🔗 SIMILAR OPTIONS ({len(additional)}):")
            for i, product in enumerate(additional, 1):
                print(f"  {i}. {product['name']} - {product['price']}")
        
        if result["budget_applied"]:
            print(f"\n💰 Budget Filter: ${result['budget_range'][0]:.0f} - ${result['budget_range'][1]:.0f}")
    
    else:
        print(f"❌ Error: {result['error']}")

def run_comprehensive_tests():
    """Run comprehensive tests"""
    test_queries = [
        # Exact product queries
        "iPhone 15 Pro",
        "Samsung Galaxy S24",
        "MacBook Pro M3",
        
        # Category queries with budget
        "wireless headphones under $200",
        "gaming laptop under $1500",
        "budget smartphone under $300",
        
        # Feature-based queries
        "noise cancelling headphones",
        "mechanical keyboard for gaming",
        "bluetooth earbuds with long battery",
        
        # Generic queries
        "headphones",
        "laptop for work",
        "phone with good camera"
    ]
    
    print("🧪 COMPREHENSIVE FUNCTION CALL TESTS")
    print("=" * 80)
    
    for query in test_queries:
        test_query(query)

if __name__ == "__main__":
    print("🤖 Enhanced Voice Agent Function Call Tool")
    print("Database Search with Exact Match Detection")
    print("=" * 60)
    
    # Quick test
    # test_query("I want headphones under $200")
    run_comprehensive_tests()
    
    print("\n" + "="*60)
    print("🚀 For comprehensive testing, call: run_comprehensive_tests()")
    print("🎯 Usage: result = function_call('user query here')")