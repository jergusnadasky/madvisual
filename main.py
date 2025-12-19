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
                a_count = cumulative.get("aCount", 0)
                ab_count = cumulative.get("abCount", 0)
                b_count = cumulative.get("bCount", 0)
                bc_count = cumulative.get("bcCount", 0)
                c_count = cumulative.get("cCount", 0)
                d_count = cumulative.get("dCount", 0)
                f_count = cumulative.get("fCount", 0)
                
                # Calculate GPA (A=4.0, AB=3.5, B=3.0, BC=2.5, C=2.0, D=1.0, F=0.0)
                total_points = (a_count * 4.0 + ab_count * 3.5 + b_count * 3.0 + 
                               bc_count * 2.5 + c_count * 2.0 + d_count * 1.0 + f_count * 0.0)
                gpa = total_points / total if total > 0 else 0.0
                
                grades_data = {
                    "total": total,
                    "a_percent": (a_count / total) * 100,
                    "ab_percent": (ab_count / total) * 100,
                    "b_percent": (b_count / total) * 100,
                    "bc_percent": (bc_count / total) * 100,
                    "c_percent": (c_count / total) * 100,
                    "d_percent": (d_count / total) * 100,
                    "f_percent": (f_count / total) * 100,
                    "gpa": gpa
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
        "gpa": 0.0,
        "rmp_rating": 3.0,  # Placeholder until we add RMP integration
        "optimal_score": 0.0,
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
            course_data["gpa"] = grades_data.get("gpa", 0.0)
        else:
            course_data["error"] = grades_error or "No cumulative grade data available (treated as 0% A's)"
    else:
        course_data["error"] = error or "Course not found (treated as 0% A's)"
    
    return course_data

def calculate_optimal_score(a_percent, rmp_rating, gpa, weight_grade, weight_rmp, weight_gpa):
    """Calculate weighted optimal score (0-100 scale)"""
    # Normalize each metric to 0-100 scale
    a_score = a_percent  # already 0-100
    rmp_score = (rmp_rating / 5.0) * 100  # RMP is 0-5, convert to 0-100
    gpa_score = (gpa / 4.0) * 100  # GPA is 0-4, convert to 0-100
    
    # Weighted average
    optimal_score = (a_score * weight_grade) + (rmp_score * weight_rmp) + (gpa_score * weight_gpa)
    return optimal_score

@app.route("/", methods=["GET", "POST"])
def index():
    if request.method == "POST":
        courses_input = request.form["courses"]
        parsed_courses = parse_courses(courses_input)
        
        # Get user preference weights (normalize to ensure they sum to 1.0)
        weight_grade = float(request.form.get("weight_grade", 50))
        weight_rmp = float(request.form.get("weight_rmp", 30))
        weight_gpa = float(request.form.get("weight_gpa", 20))
        total_weight = weight_grade + weight_rmp + weight_gpa
        
        if total_weight != 100:
            return render_template(
                "index.html",
                error="Total weight must equal 100%. Please adjust your sliders.",
             )

        
        # Normalize weights
        if total_weight > 0:
            weight_grade /= total_weight
            weight_rmp /= total_weight
            weight_gpa /= total_weight
        else:
            weight_grade, weight_rmp, weight_gpa = 0.5, 0.3, 0.2
        
        print(f"User weights: Grade={weight_grade:.2f}, RMP={weight_rmp:.2f}, GPA={weight_gpa:.2f}")
        
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
        
        # Calculate optimal scores for each course
        for course in courses:
            course["optimal_score"] = calculate_optimal_score(
                course["a_percent"],
                course["rmp_rating"],
                course["gpa"],
                weight_grade,
                weight_rmp,
                weight_gpa
            )
        
        # Sort courses by optimal score (highest first)
        courses.sort(key=lambda c: c["optimal_score"], reverse=True)
        
        # Recommendation
        if courses:
            best_course = courses[0]  # First item is now the best
            recommendation = f"{best_course['subject']} {best_course['number']}"
            if best_course['name']:
                recommendation += f" - {best_course['name']}"
            recommendation += f" (Score: {best_course['optimal_score']:.1f}/100)"
            if best_course["optimal_score"] == 0:
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
                "optimal_score": f"{c['optimal_score']:.1f}",
                "a_percent": f"{c['a_percent']:.2f}%",
                "gpa": f"{c['gpa']:.2f}",
                "rmp_rating": f"{c['rmp_rating']:.1f}/5.0",
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