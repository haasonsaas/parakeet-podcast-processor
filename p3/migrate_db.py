"""Database migration script for adding error tracking to P³."""

import duckdb
from pathlib import Path

def migrate_database(db_path: str = "data/p3.duckdb"):
    """Add error tracking columns to existing database."""
    print("Migrating database to add error tracking...")
    
    conn = duckdb.connect(str(db_path))
    
    # Check if columns already exist
    try:
        result = conn.execute("""
            SELECT column_name 
            FROM information_schema.columns 
            WHERE table_name = 'episodes' 
            AND column_name IN ('error_count', 'last_error', 'error_timestamp')
        """).fetchall()
        
        existing_columns = [row[0] for row in result]
        
        # Add missing columns
        if 'error_count' not in existing_columns:
            conn.execute("ALTER TABLE episodes ADD COLUMN error_count INTEGER DEFAULT 0")
            print("  ✓ Added error_count column")
        
        if 'last_error' not in existing_columns:
            conn.execute("ALTER TABLE episodes ADD COLUMN last_error TEXT")
            print("  ✓ Added last_error column")
        
        if 'error_timestamp' not in existing_columns:
            conn.execute("ALTER TABLE episodes ADD COLUMN error_timestamp TIMESTAMP")
            print("  ✓ Added error_timestamp column")
        
        # Update any episodes that might be stuck to have proper status
        conn.execute("""
            UPDATE episodes 
            SET status = 'downloaded' 
            WHERE status NOT IN ('downloaded', 'transcribed', 'processed', 'failed')
        """)
        
        conn.commit()
        print("✓ Database migration completed successfully!")
        
    except Exception as e:
        print(f"Migration error: {e}")
        return False
    
    finally:
        conn.close()
    
    return True

if __name__ == "__main__":
    migrate_database()