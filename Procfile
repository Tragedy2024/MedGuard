release: cd backend && python -m pip install -r requirements.txt
web: cd backend && python -m uvicorn backend.main:app --host 0.0.0.0 --port $PORT