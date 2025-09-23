import json
import numpy as np
import faiss
import os
from typing import Dict, Tuple
from openai import OpenAI

# Load OpenAI API key from environment variable
from dotenv import load_dotenv
load_dotenv()
api_key = os.getenv("OPENAI_API_KEY")
if not api_key:
    raise ValueError("OPENAI_API_KEY environment variable is not set.")

# Initialize OpenAI client with API key
client = OpenAI(api_key=api_key)

# Load product database
with open('test_products.json', 'r') as f:
    PRODUCT_DATABASE = json.load(f)

def embed_text(text: str) -> np.ndarray:
    """Generate embedding vector using OpenAI embeddings API"""
    response = client.embeddings.create(
        input=text,
        model="text-embedding-3-small"  # or another available embedding model
    )
    embedding = response.data[0].embedding
    return np.array(embedding, dtype=np.float32)

def create_faiss_index() -> Tuple[faiss.Index, Dict[int, dict]]:
    """Create FAISS index for product search using OpenAI embeddings"""
    print("Creating FAISS index with OpenAI embeddings...")
    
    embeddings = []
    id_to_product = {}
    
    for i, product in enumerate(PRODUCT_DATABASE):
        # Combine product text fields for embedding
        text = f"{product['name']} {product['category']} {product['description']} {' '.join(product.get('features', []))}"
        embedding = embed_text(text)
        embeddings.append(embedding)
        id_to_product[i] = product
    
    embeddings_array = np.array(embeddings).astype('float32')
    index = faiss.IndexFlatIP(embeddings_array.shape[1])  # Inner product index for similarity
    index.add(embeddings_array)
    
    print(f"✅ Created FAISS index with {len(embeddings)} products")
    return index, id_to_product

def load_index_and_mapping():
    """Load or create FAISS index and id-to-product mapping"""
    try:
        return create_faiss_index()
    except Exception as e:
        print(f"Error creating index: {e}")
        return None, {}

# Create index when module is run directly
if __name__ == "__main__":
    index, mapping = create_faiss_index()
    print("FAISS index created successfully!")
