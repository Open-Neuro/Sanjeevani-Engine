# 💊 ShivanyaRx AI - Backend System 🧠

![FastAPI](https://img.shields.io/badge/FastAPI-005571?style=for-the-badge&logo=fastapi)
![Python](https://img.shields.io/badge/Python-3.11+-3776AB?style=for-the-badge&logo=python&logoColor=white)
![MongoDB](https://img.shields.io/badge/MongoDB-4EA94B?style=for-the-badge&logo=mongodb&logoColor=white)

Welcome to the **Backend System** of ShivanyaRx AI! This is the "brain" of our pharmacy platform. It handles all the data, powers the AI predictions for medicine refills, manages inventory intelligence, and connects all our apps (Web & Mobile) together. 

It is built completely with **Python** using the super-fast **FastAPI** framework and connects to a **MongoDB** database. 🚀

---
## 🌟 Core Features
* **🧠 AI Refill Predictions:** Analyzes past orders to predict when a patient needs their next refill.
* **📦 Smart Inventory Management:** Tracks stock levels and alerts on low quantities.
* **💬 Langfuse Analytics:** Connects patient interactions with AI insights.
* **🔒 Secure FastAPIs:** Robust endpoints ready to be consumed by the frontend and mobile app.


## ✨ Getting Started for Beginners (Local Setup)

Don't worry if this is your first time setting this up. Just follow these steps one by one!

### 1️⃣ What You Need Installed
Before starting, ensure you have these installed on your machine:
* **Python (3.11 or newer)**: [Download here](https://www.python.org/downloads/)
* **MongoDB**: You can run it locally or use a cloud version. 

### 2️⃣ Setup Your Virtual Environment
A virtual environment keeps our project packages isolated so they don't mess with your computer. In your terminal (inside this folder), run:
```bash
# Create the virtual environment
python -m venv .venv

# Activate it (Windows)
.venv\Scripts\activate

# Activate it (Mac/Linux)
source .venv/bin/activate
```
*(You should see `(.venv)` appear in your terminal prompt!)*

### 3️⃣ Install Required Packages
Now, let's install everything the backend needs to run:
```bash
pip install -r requirements.txt
```

### 4️⃣ Configure Settings Environment
We need to tell the app where the database and secret keys are. 
1. Copy the `.env.example` file and paste it to create a new file named `.env`.
2. Open `.env` and configure the following essential variables:
   * `MONGO_URI`: The connection string to your MongoDB (local or Atlas).
   * `GROQ_API_KEY`: Your API key for powering the AI features.
   * `LANGFUSE_PUBLIC_KEY` & `LANGFUSE_SECRET_KEY`: (Optional) Keys for AI analytics if you have them.

### 5️⃣ Load Sample Data
Let's populate the database with some testing data (patients, orders, inventory) so the app isn't empty!
```bash
python scripts/load_data.py --orders data/consumer_orders.xlsx --products data/products.xlsx
```

### 6️⃣ Run the API Server! 🎉
Start the server with this command:
```bash
uvicorn app.main:app --reload
```
That's it! 🥳 You can now visit:
* **http://localhost:8000** - The API root
* **http://localhost:8000/api/v1/docs** - The interactive Swagger UI where you can **test all APIs easily!** This is super helpful for frontend and mobile devs!

---

## 🏗️ Project Structure Explained

* 📁 **`app/`** - The core application code.
  * `api/` - Contains all our endpoints (like `/products`, `/orders`, `/dashboard`).
  * `database/` - Everything related to connecting and interacting with MongoDB.
  * `modules/` - The "brain" logic (AI predictions, safety checks, inventory intelligence).
* 📁 **`scripts/`** - Helper scripts for doing tasks like loading data or running batch predictions.
* 📁 **`tests/`** - Where we write tests to make sure everything works perfectly.

---

## 🐳 Docker Setup (The "One-Click" Way)

If you have **Docker** installed, skipping the manual setup is super easy:
```bash
# This will build and run the backend AND MongoDB automatically!
docker-compose up --build
```
Your API will be running on `http://localhost:8000` just like that! To view the database, you can connect to `localhost:27017`.

---

## 🧪 Testing and Analytics

* To run batch AI predictions for refills: 
  ```bash
  python scripts/generate_predictions.py
  ```
* To run the automated tests:
  ```bash
  pytest tests/ -v
  ```

---

## 🛠️ Troubleshooting Common Issues

* **Database Connection Error (`pymongo.errors.ServerSelectionTimeoutError`)**: 
  Ensure your MongoDB instance is running locally or that your `MONGO_URI` in the `.env` file is correct and accessible.
* **Port In Use (8000)**: 
  If `uvicorn` fails to start because the port is already taken, you can safely run it on a different port: `uvicorn app.main:app --reload --port 8001`.
* **AI Features Failing**: 
  Double-check that your `GROQ_API_KEY` is correctly set in your `.env` file and hasn't expired.

---

## 🤝 For the Team
If you're building a new feature:
1. Always add your models in `app/database/models.py`.
2. Try to keep your API endpoints clean and well-documented.
3. For testing APIs visually, always use the awesome `/api/v1/docs` page.

Happy Coding! ✨🔥
