"""Supabase client — thin wrapper for auth and database access."""
import os
from supabase import create_client, Client

SUPABASE_URL = os.getenv("SUPABASE_URL", "https://oizcobouxfbvmcdksfwd.supabase.co")
SUPABASE_ANON_KEY = os.getenv("SUPABASE_ANON_KEY", "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Im9pemNvYm91eGZidm1jZGtzZndkIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NzU2ODA0MjcsImV4cCI6MjA5MTI1NjQyN30.6hw9B8RUB8Rq558qOynb5oCTGcYODBSJ5zeDRVwd_V8")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_ANON_KEY)
