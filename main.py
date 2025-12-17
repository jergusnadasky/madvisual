from flask import Flask, render_template, request
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed

app = Flask(__name__)

API_BASE = "https://api.madgrades.com/v1"
API_TOKEN = "12a5beecf9e240efb0a8748e6ee91aaa"

# Use a session for connection pooling
session = requests.Session()

def get_headers():
    return {"Authorization": f"Token token={API_TOKEN}"}

def search_course(subject, course_number):
    query = f"{subject} {course_number}"
    params = {"query": query}
    try:
        response = session.get(f"{API_BASE}/courses", params=params, headers=get_headers(), timeout=10)
        print(f"Search for {query}: Status {response.status_code}")
        if response.status_code == 200:
            data = response.json()
            results = data.get("results", [])
            if results:
                # Try to find exact match by checking course number in results
                for result in results:
                    result_number = result.get("number")
                    # Match if the number appears in the course number field
                    if result_number and str(result_number) == str(course_number):
                        uuid = result["uuid"]
                        name = result.get("name", "Unknown Course")
                        print(f"  -> Exact match: {name} (UUID: {uuid}, Number: {result_number})")
                        return uuid, name, None
                
                # Fallback to first result if no exact match
                uuid = results[0]["uuid"]
                name = results[0].get("name", "Unknown Course")
                result_number = results[0].get("number", "?")
                print(f"  -> First result (no exact match): {name} (UUID: {uuid}, Number: {result_number})")
                return uuid, name, None
            return None, None, f"No courses found for {query}"
        return None, None, f"API error: {response.status_code}"
    except Exception as e:
        return None, None, f"Exception: {str(e)}"

def get_course_grades(uuid):
    try:
        response = session.get(f"{API_BASE}/courses/{uuid}/grades", headers=get_headers(), timeout=10)
        print(f"Grades for UUID {uuid}: Status {response.status_code}")
        if response.status_code == 200:
            data = response.json()
            cumulative = data.get("cumulative", {})
            if cumulative and cumulative.get("total", 0) > 0:
                total = cumulative.get("total", 0)
                grades_data = {
                    "total": total,
                    "a_percent": (cumulative.get("aCount", 0) / total) * 100,
                    "ab_percent": (cumulative.get("abCount", 0) / total) * 100,
                    "b_percent": (cumulative.get("bCount", 0) / total) * 100,
                    "bc_percent": (cumulative.get("bcCount", 0) / total) * 100,
                    "c_percent": (cumulative.get("cCount", 0) / total) * 100,
                    "d_percent": (cumulative.get("dCount", 0) / total) * 100,
                    "f_percent": (cumulative.get("fCount", 0) / total) * 100,
                }
                return grades_data, None
            return {}, "No cumulative grade data available"
        return {}, f"API error: {response.status_code}"
    except Exception as e:
        return {}, f"Exception: {str(e)}"

def parse_courses(input_str):
    if input_str.startswith("SELECT FROM:"):
        input_str = input_str[12:].strip()
    raw_tokens = input_str.replace('\n', ' ').split()
    tokens = []
    for rt in raw_tokens:
        if ',' in rt:
            for num in rt.split(','):
                if num:
                    tokens.append(num)
        else:
            tokens.append(rt)
    
    courses = []
    courses_set = set()  # Track duplicates
    current_subject_parts = []
    in_dept = True
    for token in tokens:
        if token in ["SELECT", "FROM:"]:
            continue
        if token == "OR":
            continue
        if token.isalpha() and token.isupper():
            if in_dept:
                current_subject_parts.append(token)
            else:
                in_dept = True
                current_subject_parts = [token]
        else:
            try:
                int(token)
            except ValueError:
                continue
            subject = ' '.join(current_subject_parts)
            if subject:
                course_key = (subject, token)
                if course_key not in courses_set:  # Only add if not duplicate
                    courses.append(course_key)
                    courses_set.add(course_key)
            in_dept = False
    return courses

def process_single_course(subject, course_number):
    """Process a single course and return all its data"""
    course_data = {
        "subject": subject,
        "number": course_number,
        "name": None,
        "uuid": None,
        "grades": {},
        "a_percent": 0.0,
        "error": None
    }
    
    print(f"\nProcessing {subject} {course_number}")
    uuid, name, error = search_course(subject, course_number)
    
    if uuid:
        course_data["uuid"] = uuid
        course_data["name"] = name
        grades_data, grades_error = get_course_grades(uuid)
        course_data["grades"] = grades_data
        
        if grades_data and grades_data.get("total", 0) > 0:
            course_data["a_percent"] = grades_data["a_percent"]
        else:
            course_data["error"] = grades_error or "No cumulative grade data available (treated as 0% A's)"
    else:
        course_data["error"] = error or "Course not found (treated as 0% A's)"
    
    return course_data

@app.route("/", methods=["GET", "POST"])
def index():
    if request.method == "POST":
        courses_input = request.form["courses"]
        parsed_courses = parse_courses(courses_input)
        
        # Process courses concurrently with ThreadPoolExecutor
        courses = []
        with ThreadPoolExecutor(max_workers=10) as executor:
            # Submit all tasks
            future_to_course = {
                executor.submit(process_single_course, subj, num): (subj, num)
                for subj, num in parsed_courses
            }
            
            # Collect results as they complete
            for future in as_completed(future_to_course):
                try:
                    course_data = future.result()
                    courses.append(course_data)
                except Exception as e:
                    subj, num = future_to_course[future]
                    print(f"Error processing {subj} {num}: {e}")
                    courses.append({
                        "subject": subj,
                        "number": num,
                        "name": None,
                        "uuid": None,
                        "grades": {},
                        "a_percent": 0.0,
                        "error": f"Processing error: {str(e)}"
                    })
        
        # Sort courses by A percentage (highest first)
        courses.sort(key=lambda c: c["a_percent"], reverse=True)
        
        # Recommendation
        if courses:
            best_course = courses[0]  # First item is now the best
            recommendation = f"{best_course['subject']} {best_course['number']}"
            if best_course['name']:
                recommendation += f" - {best_course['name']}"
            recommendation += f" ({best_course['a_percent']:.2f}% A's)"
            if best_course["a_percent"] == 0:
                recommendation += " (All courses lack data; consider manual review)"
        else:
            recommendation = "No courses provided."
        
        # Create results with ranking
        results = []
        for rank, c in enumerate(courses, 1):
            results.append({
                "rank": rank,
                "course": f"{c['subject']} {c['number']}",
                "name": c['name'] or "Unknown",
                "a_percent": f"{c['a_percent']:.2f}%",
                "ab_percent": f"{c['grades'].get('ab_percent', 0):.2f}%" if c['grades'] else "0.00%",
                "b_percent": f"{c['grades'].get('b_percent', 0):.2f}%" if c['grades'] else "0.00%",
                "bc_percent": f"{c['grades'].get('bc_percent', 0):.2f}%" if c['grades'] else "0.00%",
                "c_percent": f"{c['grades'].get('c_percent', 0):.2f}%" if c['grades'] else "0.00%",
                "d_percent": f"{c['grades'].get('d_percent', 0):.2f}%" if c['grades'] else "0.00%",
                "f_percent": f"{c['grades'].get('f_percent', 0):.2f}%" if c['grades'] else "0.00%",
                "total": c['grades'].get('total', 0) if c['grades'] else 0,
                "error": c["error"]
            })
        
        print(f"\nRecommendation: {recommendation}")
        return render_template("results.html", results=results, recommendation=recommendation)
    
    return render_template("index.html")

if __name__ == "__main__":
    app.run(debug=True)
