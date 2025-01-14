import json
import os
import logging

from flask import Flask, render_template, request, redirect, url_for, flash
from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.instrumentation.flask import FlaskInstrumentor
from opentelemetry.exporter.jaeger.thrift import JaegerExporter
from opentelemetry.trace import SpanKind

# Flask App Initialization
app = Flask(__name__)
app.secret_key = 'secret' # Required for flashing messages
COURSE_FILE = 'course_catalog.json' # JSON file to store course data
error_count=0 # Count of number of errors.

# Set Flask logger level
app.logger.setLevel(logging.INFO)

# Configure logging to export to a .json file
log_handler = logging.FileHandler('app_logs.json')
log_handler.setLevel(logging.INFO)
log_formatter = logging.Formatter(
    json.dumps({
        "time": "%(asctime)s",
        "level": "%(levelname)s",
        "message": "%(message)s"
    },indent=4)
)
log_handler.setFormatter(log_formatter)
app.logger.addHandler(log_handler)

# OpenTelemetry Setup
resource = Resource.create({"service.name": "course-catalog-service"})
trace.set_tracer_provider(TracerProvider(resource=resource))
tracer = trace.get_tracer(__name__)
jaeger_exporter = JaegerExporter(
    agent_host_name="localhost",
    agent_port=6831,
)
span_processor = BatchSpanProcessor(jaeger_exporter)
trace.get_tracer_provider().add_span_processor(span_processor)
FlaskInstrumentor().instrument_app(app)

# Initialize catalog access count
catalog_access_count = 0

# Initialize set to track logged IPs
logged_ips = set()

# Utility Functions
def load_courses():
    """Load courses from the JSON file."""
    if not os.path.exists(COURSE_FILE):
        return []  # Return an empty list if the file doesn't exist
    with open(COURSE_FILE, 'r') as file:
        return json.load(file)

def save_courses(data):
    """Save new course data to the JSON file."""
    required_fields = ['code', 'name']
    missing_fields = [field for field in required_fields if field not in data or not data[field]] # Check for missing fields
    global error_count

    courses = load_courses()  # Load existing courses
    courses.append(data)  # Append the new course
    try:
        # Save the updated course list to the file
        with open(COURSE_FILE, 'w') as file:
            json.dump(courses, file, indent=6)
        app.logger.info(f"Course '{data['name']}' added with code '{data['code']}'")
    except Exception as e:
        # Logging the error message and incrementing the error count
        error_count+=1
        app.logger.error(f"Error saving course data: {str(e)}")
        with tracer.start_as_current_span("save_courses_error", kind=SpanKind.INTERNAL) as span:
            # Adding error attributes to the span (error type, error count) and logging the error message
            span.set_attribute("error.type", "FileWriteError")
            span.set_attribute("error.count", error_count)
            span.add_event(f"Error saving course data: {str(e)}")
    if missing_fields:
        # Logging the error message and incrementing the error count
        error_message = f"Missing required fields: {', '.join(missing_fields)}"
        app.logger.error(error_message)
        flash(error_message, "error")

        error_count+=1
        with tracer.start_as_current_span("save_courses_error", kind=SpanKind.INTERNAL) as span:
            # Adding error attributes to the span
            span.set_attribute("error.type", "MissingFields")
            span.set_attribute("error.count", error_count) 
            span.add_event(error_message)
        return  # Exit the function if there are missing fields
    flash(f"Course '{data['name']}' added successfully!", "success")

# Routes
@app.route('/')
def index():
    user_ip = request.remote_addr
    if (user_ip not in logged_ips):
        app.logger.info(f"User IP: {user_ip}")
        logged_ips.add(user_ip)
    with tracer.start_as_current_span("index", kind=SpanKind.SERVER) as span:
        span.set_attribute("http.client_ip", user_ip)
        return render_template('index.html')

@app.route('/catalog')
def course_catalog():
    global catalog_access_count
    catalog_access_count += 1

    with tracer.start_as_current_span("course_catalog", kind=SpanKind.SERVER) as span:
        span.set_attribute("http.method", request.method)
        span.set_attribute("http.url", request.url)
        
        span.set_attribute("catalog.access_count", catalog_access_count)
        span.add_event("Loading courses from file")
        courses = load_courses()
        span.set_attribute("course.count", len(courses))
        span.add_event("Rendering course catalog template")
        app.logger.info("Course catalog page rendered successfully")
        return render_template('course_catalog.html', courses=courses)

@app.route('/add_course', methods=['GET', 'POST'])
def add_course():
    if request.method == 'POST':
        with tracer.start_as_current_span("add_course", kind=SpanKind.SERVER) as span:
            span.set_attribute("http.method", request.method)
            span.set_attribute("http.url", request.url)
            span.set_attribute("http.client_ip", request.remote_addr)
            span.add_event("Extracting course data from form")
            course = {
                'code': request.form['code'],
                'name': request.form['name'],
                'instructor': request.form['instructor'],
                'semester': request.form['semester'],
                'schedule': request.form['schedule'],
                'classroom': request.form['classroom'],
                'prerequisites': request.form['prerequisites'],
                'grading': request.form['grading'],
                'description': request.form['description']
            }
            span.set_attribute("course.code", course['code'])
            span.set_attribute("course.name", course['name'])
            span.add_event("Saving course data to file")
            save_courses(course)
            
            return redirect(url_for('course_catalog'))
    return render_template('add_course.html')

@app.route('/course/<code>')
def course_details(code):
    with tracer.start_as_current_span("course_details", kind=SpanKind.SERVER) as span:
        span.set_attribute("http.method", request.method)
        span.set_attribute("http.url", request.url)
        span.set_attribute("http.client_ip", request.remote_addr)
        span.add_event("Loading courses from file")
        courses = load_courses()
        span.set_attribute("course.count", len(courses))
        span.add_event(f"Searching for course with code {code}")
        course = next((course for course in courses if course['code'] == code), None)
        if not course:
            flash(f"No course found with code '{code}'.", "error")
            return redirect(url_for('course_catalog'))
        span.set_attribute("course.code", course['code'])
        span.set_attribute("course.name", course['name'])
        span.add_event("Rendering course details template")
        return render_template('course_details.html', course=course)

@app.route("/manual-trace")
def manual_trace():
    # Start a span manually for custom tracing
    with tracer.start_as_current_span("manual-span", kind=SpanKind.SERVER) as span:
        span.set_attribute("http.method", request.method)
        span.set_attribute("http.url", request.url)
        span.set_attribute("http.client_ip", request.remote_addr)
        span.add_event("Processing request")
        return "Manual trace recorded!", 200

@app.route("/auto-instrumented")
def auto_instrumented():
    # Automatically instrumented via FlaskInstrumentor
    return "This route is auto-instrumented!", 200

if __name__ == '__main__':
    app.run(debug=True)
