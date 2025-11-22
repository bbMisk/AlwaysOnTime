import os
from dotenv import load_dotenv
from .factory import create_app

load_dotenv()
app = create_app()

if __name__ == "__main__":
    port = int(os.getenv("PORT", "5001"))
    app.run(host="0.0.0.0", port=port, debug=True)
