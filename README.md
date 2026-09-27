# RULER (Repeat Unit Loci Evaluator for Relatedness)

## Installation & Setup

This project is completely containerized using Docker. All system dependencies are automatically configured inside the containers.

### Prerequisites

* **Docker & Docker Compose**
* **Standalone Tailwind CLI** (for compiling frontend assets)

---

#### 1. Environment Configuration
Create a `.env` file in the root directory to define your environment variables:

```env
SECRET_KEY=your_secret_key_here
WEB_EXTERNAL_PORT=1337
REDIS_EXTERNAL_PORT=6380
```

#### 2. Frontend Setup (Tailwind CSS & DaisyUI)
We use the standalone Tailwind/DaisyUI binary so you do not need Node.js installed on your machine.

Run the installer in the project root to download the executable:

```bash
# For Linux or macOS:
curl -sL https://daisyui.com/fast | bash
./tailwindcss -i ./core/static/css/input.css -o ./core/static/css/output.css

# For Windows (PowerShell):
powershell -c "irm https://daisyui.com/fast.ps1 | iex"
tailwindcss.exe -i .\core\static\css\input.css -o .\core\static\css\output.css
```

To watch for style changes during development, keep this compiler running in a separate terminal window. It will continuously build your CSS directly into the project's static directory, which Docker instantly mirrors:

```bash
# For Linux/macOS:
./tailwindcss -i ./core/static/css/input.css -o ./core/static/css/output.css --watch

# For Windows:
tailwindcss.exe -i .\core\static\css\input.css -o .\core\static\css\output.css --watch
```

#### 3. Build and Start the Application
First, ensure Docker is running. In your main terminal, run the following command to build the images and launch the web server, Redis instance, and background worker:

```bash
docker compose up --build
```

The web application will be live at http://localhost:1337 (or your custom WEB_EXTERNAL_PORT).

Existing database migrations are applied automatically every time the containers start up.

##### Working with Database Migrations
Because the application runs inside Docker, you must explicitly tell the container when to generate new migration files after you modify your Django models.

##### Creating New Migrations
When you make changes to any models.py file, spin up a temporary container to generate the migration files:

```bash
docker compose run --rm web python manage.py makemigrations
```

This will write the new migration files to your host machine's directory via Docker's volume mapping, making them ready to be tracked and committed to Git.

##### Applying Migrations
Once generated, you can apply them immediately without restarting your entire setup by running:

```bash
docker compose exec web python manage.py migrate
```

(Alternatively, simply stopping and restarting docker compose up will apply them automatically).

