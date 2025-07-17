from datetime import date, timedelta
from garminconnect import Garmin
from notion_client import Client
from dotenv import load_dotenv
import os
import time

def get_all_daily_steps(garmin, retry_count=0):
    """Get last x days of daily step count data from Garmin Connect."""
    startdate = date.today() - timedelta(days=7)
    daterange = [startdate + timedelta(days=x) for x in range((date.today() - startdate).days + 1)]
    daily_steps = []
    for d in daterange:
        try:
            steps = garmin.get_daily_steps(d.isoformat(), d.isoformat())
            daily_steps += steps
            time.sleep(0.5)
        except Exception as e:
            print(f"Error getting steps for {d}: {e}")
            if "429" in str(e) or "Too Many Requests" in str(e):
                retry_after = 10 * (2 ** retry_count)
                print(f"Rate limit hit (attempt {retry_count + 1}), waiting {retry_after} seconds...")
                time.sleep(retry_after)
                if retry_count < 3:
                    return get_all_daily_steps(garmin, retry_count + 1)
            else:
                print(f"Skipping steps for {d} due to error")
    return daily_steps

def daily_steps_exist(client, database_id, activity_date):
    """Check if daily step count already exists in the Notion database."""
    query = client.databases.query(
        database_id=database_id,
        filter={"and": [
            {"property": "Date", "date": {"equals": activity_date}},
            {"property": "Activity Type", "title": {"equals": "Walking"}}
        ]}
    )
    return query['results'][0] if query['results'] else None

def steps_need_update(existing_steps, new_steps):
    """Compare existing steps data with imported data to determine if an update is needed."""
    existing_props = existing_steps['properties']
    return (
        existing_props['Total Steps']['number'] != new_steps.get('totalSteps') or
        existing_props['Step Goal']['number'] != new_steps.get('stepGoal') or
        existing_props['Total Distance (km)']['number'] != new_steps.get('totalDistance') or
        existing_props['Activity Type']['title'] != "Walking"
    )

def update_daily_steps(client, existing_steps, new_steps):
    """Update an existing daily steps entry in the Notion database with new data."""
    total_distance = new_steps.get('totalDistance', 0)
    properties = {
        "Activity Type": {"title": [{"text": {"content": "Walking"}}]},
        "Total Steps": {"number": new_steps.get('totalSteps')},
        "Step Goal": {"number": new_steps.get('stepGoal')},
        "Total Distance (km)": {"number": round(total_distance / 1000, 2)}
    }
    client.pages.update(page_id=existing_steps['id'], properties=properties)

def create_daily_steps(client, database_id, steps):
    """Create a new daily steps entry in the Notion database."""
    total_distance = steps.get('totalDistance', 0)
    properties = {
        "Activity Type": {"title": [{"text": {"content": "Walking"}}]},
        "Date": {"date": {"start": steps.get('calendarDate')}},
        "Total Steps": {"number": steps.get('totalSteps')},
        "Step Goal": {"number": steps.get('stepGoal')},
        "Total Distance (km)": {"number": round(total_distance / 1000, 2)}
    }
    client.pages.create(parent={"database_id": database_id}, properties=properties)

def get_sleep_data(garmin, date, retry_count=0):
    """Get sleep data for a specific date"""
    try:
        sleep_data = garmin.get_sleep_data(date)
        time.sleep(0.5)
        return sleep_data
    except Exception as e:
        print(f"Error getting sleep data for {date}: {e}")
        if "429" in str(e) or "Too Many Requests" in str(e):
            retry_after = 10 * (2 ** retry_count)
            print(f"Rate limit hit (attempt {retry_count + 1}), waiting {retry_after} seconds...")
            time.sleep(retry_after)
            if retry_count < 3:
                return get_sleep_data(garmin, date, retry_count + 1)
        return None

def extract_sleep_metrics(sleep_data):
    """Extract sleep duration and score from sleep data."""
    sleep_duration = sleep_score = 0
    if not sleep_data:
        return sleep_duration, sleep_score
    
    if isinstance(sleep_data, list) and sleep_data:
        sleep_entry = sleep_data[0]
        sleep_duration = sleep_entry.get('sleepTimeSeconds', 0) / 3600
        sleep_score = sleep_entry.get('sleepScore', 0)
    elif isinstance(sleep_data, dict):
        if 'dailySleepDTO' in sleep_data:
            daily_sleep = sleep_data['dailySleepDTO']
            sleep_duration = daily_sleep.get('sleepTimeSeconds', 0) / 3600
            if 'sleepScores' in daily_sleep and daily_sleep['sleepScores']:
                overall = daily_sleep['sleepScores'].get('overall', {})
                if isinstance(overall, dict) and 'value' in overall:
                    sleep_score = int(overall['value'])
                elif isinstance(overall, (int, float, str)):
                    sleep_score = int(overall)
        else:
            sleep_duration = sleep_data.get('sleepTimeSeconds', 0) / 3600
            sleep_score = sleep_data.get('sleepScore', 0)
    
    return sleep_duration, sleep_score

def update_wellness_database(client, wellness_database_id, steps, garmin):
    """Update wellness database using existing steps data and sleep data"""
    if not wellness_database_id:
        return
    
    steps_date = steps.get('calendarDate')
    total_steps = steps.get('totalSteps', 0)
    step_goal = steps.get('stepGoal', 10000)
    
    # Get sleep data and extract metrics
    sleep_data = get_sleep_data(garmin, steps_date)
    sleep_duration, sleep_score = extract_sleep_metrics(sleep_data)
    
    # Calculate goals
    steps_goal_met = total_steps >= step_goal
    sleep_goal_met = sleep_duration >= 7 and sleep_score >= 85
    
    # Check for exercise activities
    exercise_activities = client.databases.query(
        database_id=os.getenv("NOTION_DB_ID"),
        filter={"and": [
            {"property": "Date", "date": {"equals": steps_date}},
            {"property": "Activity Type", "select": {"does_not_equal": "Walking"}}
        ]}
    )
    exercise_logged = len(exercise_activities['results']) > 0
    
    # Check if wellness entry exists
    existing_entry = client.databases.query(
        database_id=wellness_database_id,
        filter={"property": "Date", "date": {"equals": steps_date}}
    )
    
    wellness_properties = {
        "Date": {"date": {"start": steps_date}},
        "15000 Steps": {"checkbox": steps_goal_met},
        "😴7+ hrs Sleep, 85+ Score": {"checkbox": sleep_goal_met},
        "🏃🏾‍♂️ Excersice": {"checkbox": exercise_logged}
    }
    
    try:
        if existing_entry['results']:
            client.pages.update(
                page_id=existing_entry['results'][0]['id'],
                properties=wellness_properties
            )
            print(f"Updated wellness entry for {steps_date}")
        else:
            client.pages.create(
                parent={"database_id": wellness_database_id},
                properties=wellness_properties
            )
            print(f"Created wellness entry for {steps_date}")
    except Exception as e:
        print(f"Error updating wellness entry for {steps_date}: {e}")

def main():
    load_dotenv()
    
    # Initialize clients
    garmin = Garmin(os.getenv("GARMIN_EMAIL"), os.getenv("GARMIN_PASSWORD"))
    try:
        garmin.login()
        print("Successfully logged into Garmin Connect")
    except Exception as e:
        print(f"Failed to login to Garmin Connect: {e}")
        if "429" in str(e) or "Too Many Requests" in str(e):
            print("Rate limit hit during login, waiting 10 seconds...")
            time.sleep(10)
            try:
                garmin.login()
                print("Successfully logged into Garmin Connect after retry")
            except Exception as e2:
                print(f"Failed to login after retry: {e2}")
                return
        else:
            return
    
    client = Client(auth=os.getenv("NOTION_TOKEN"))
    database_id = os.getenv("NOTION_STEPS_DB_ID")
    wellness_database_id = os.getenv("NOTION_WELLNESS_DB_ID")

    # Process daily steps
    daily_steps = get_all_daily_steps(garmin)
    for steps in daily_steps:
        steps_date = steps.get('calendarDate')
        existing_steps = daily_steps_exist(client, database_id, steps_date)
        
        if existing_steps and steps_need_update(existing_steps, steps):
            update_daily_steps(client, existing_steps, steps)
        elif not existing_steps:
            create_daily_steps(client, database_id, steps)
        
        update_wellness_database(client, wellness_database_id, steps, garmin)

if __name__ == '__main__':
    main()
