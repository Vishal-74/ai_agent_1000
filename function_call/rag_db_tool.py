import json
import numpy as np
import faiss
from sentence_transformers import SentenceTransformer
from typing import Dict, List, Tuple

# Load product database
with open('test_products.json', 'r') as f:
    PRODUCT_DATABASE = json.load(f)

# Initialize sentence transformer model
model = SentenceTransformer('all-MiniLM-L6-v2')

def embed_text(text: str) -> np.ndarray:
    """Convert text to embedding vector"""
    return model.encode(text)

def create_faiss_index() -> Tuple[faiss.Index, Dict]:
    """Create FAISS index for product search"""
    print("Creating FAISS index...")
    
    # Create embeddings for all products
    embeddings = []
    id_to_product = {}
    
    for i, product in enumerate(PRODUCT_DATABASE):
        # Create searchable text from product
        text = f"{product['name']} {product['category']} {product['description']} {' '.join(product.get('features', []))}"
        
        # Generate embedding
        embedding = embed_text(text)
        embeddings.append(embedding)
        id_to_product[i] = product
    
    # Create FAISS index
    embeddings_array = np.array(embeddings).astype('float32')
    index = faiss.IndexFlatIP(embeddings_array.shape[1])  # Inner product index
    index.add(embeddings_array)
    
    print(f"✅ Created FAISS index with {len(embeddings)} products")
    return index, id_to_product

def load_index_and_mapping():
    """Load or create FAISS index"""
    try:
        return create_faiss_index()
    except Exception as e:
        print(f"Error creating index: {e}")
        return None, {}

# Create index when module is imported
if __name__ == "__main__":
    index, mapping = create_faiss_index()
    print("FAISS index created successfully!")