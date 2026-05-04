import asyncio
import sys
sys.path.insert(0, ".")

from app.db.vectorstore import get_vectorstore, POLICY_COLLECTION_NAME

def test():
    print("Testing PGVector connection...")
    try:
        vs = get_vectorstore()
        print(f"PGVector store created successfully!")
        print(f"Collection: {POLICY_COLLECTION_NAME}")
        
        # Try a basic similarity search
        results = vs.similarity_search("leave policy", k=3)
        print(f"Search returned {len(results)} results")
        for i, doc in enumerate(results):
            source = doc.metadata.get("source", "unknown")
            print(f"  [{i+1}] Source: {source} | Preview: {doc.page_content[:80]}...")
    except Exception as e:
        print(f"ERROR: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    test()
