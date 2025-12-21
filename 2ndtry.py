import streamlit as st
import pandas as pd
import math

# ==========================================
# PART 1: THE LOGIC (Hidden from User)
# ==========================================

class FSRSModel:
    """The math engine that predicts memory decay."""
    @staticmethod
    def next_stability(stability, difficulty, rating):
        # If student rates it "Good" (3), memory grows.
        if rating >= 3:
            return round(stability * (1 + (math.exp(difficulty) * 0.1)), 2)
        # If student rates it "Forgot" (1), memory crashes.
        return round(stability * 0.5, 2)

class Translator:
    """Converts words (Easy/Hard) into numbers."""
    @staticmethod
    def get_difficulty_score(word):
        mapping = {
            "Easy (Review)": 3.0,
            "Medium (Standard)": 5.5,
            "Hard (Complex)": 8.5,
            "Nightmare (Urgent)": 10.0
        }
        return mapping.get(word, 5.5) # Default to Medium

    @staticmethod
    def get_stability_score(word):
        mapping = {
            "New / Forgot It": 0.5,
            "Vague Recall": 2.0,
            "Confident": 7.0,
            "Mastered": 14.0
        }
        return mapping.get(word, 2.0) # Default to Vague

def optimize_schedule(topics_data, slots_data):
    """
    The Bin-Packing Algorithm.
    It fits the most URGENT topics into the available TIME SLOTS.
    """
    # 1. Setup the list of topics
    # We calculate 'Urgency' = Difficulty / Current Stability
    schedule_plan = []
    
    # Sort topics: Hardest + Most Forgettable go first!
    # We use a simple lambda function to sort by Urgency Score
    topics_data.sort(key=lambda x: x['difficulty_score'] / max(x['stability_score'], 0.1), reverse=True)
    
    # 2. Iterate through time slots
    for slot in slots_data:
        time_remaining = slot['Duration (mins)']
        slot_label = slot['Slot Name']
        
        # Try to fit topics into this slot
        for topic in topics_data[:]: # Iterate over a copy so we can remove items
            if time_remaining >= 20: # We need at least 20 mins to study meaningfuly
                # Assign this topic to this slot
                schedule_plan.append({
                    "Time Slot": slot_label,
                    "Subject": topic['Topic Name'],
                    "Duration": "Full Slot", # Simplified for MVP
                    "Priority": "High"
                })
                # Remove from to-do list
                topics_data.remove(topic)
                break # Move to next slot (Simple 1-topic-per-slot rule for now)
                
    return schedule_plan, topics_data # Return plan AND leftover topics

# ==========================================
# PART 2: THE USER INTERFACE (Streamlit)
# ==========================================

def main():
    st.set_page_config(page_title="Smart Scheduler", page_icon="🎓")
    
    st.title("🎓 No-Brainer Study Planner")
    st.markdown("Don't overthink. Just fill in the tables, and we'll tell you what to do.")

    # ---------------------------------------------------------
    # STEP 1: THE SCHEDULE (Interactive Grid)
    # ---------------------------------------------------------
    st.header("Step 1: Your Free Time")
    st.info("List the empty time blocks you have today.")

    # Create a default "Starter" table so it's not empty
    default_schedule = pd.DataFrame([
        {"Slot Name": "Morning Bus", "Duration (mins)": 30},
        {"Slot Name": "After Lunch", "Duration (mins)": 45},
        {"Slot Name": "Evening Session", "Duration (mins)": 90},
    ])

    # DISPLAY THE GRID (Level 2 Feature)
    # num_rows="dynamic" lets users click "+" to add rows
    edited_schedule = st.data_editor(
        default_schedule, 
        num_rows="dynamic", 
        key="schedule_grid",
        use_container_width=True
    )

    # ---------------------------------------------------------
    # STEP 2: THE TOPICS (Interactive Grid with Dropdowns!)
    # ---------------------------------------------------------
    st.header("Step 2: Your Subjects")
    st.info("List what you need to study. Be honest about how hard it is!")

    default_topics = pd.DataFrame([
        {"Topic Name": "Math Analysis", "Difficulty": "Hard (Complex)", "Confidence": "Vague Recall"},
        {"Topic Name": "History Dates", "Difficulty": "Medium (Standard)", "Confidence": "New / Forgot It"},
    ])

    # We use column_config to turn text columns into Dropdown Menus
    topic_config = {
        "Difficulty": st.column_config.SelectboxColumn(
            "How Hard?",
            options=["Easy (Review)", "Medium (Standard)", "Hard (Complex)", "Nightmare (Urgent)"],
            required=True
        ),
        "Confidence": st.column_config.SelectboxColumn(
            "Current Memory?",
            options=["New / Forgot It", "Vague Recall", "Confident", "Mastered"],
            required=True
        )
    }

    edited_topics = st.data_editor(
        default_topics,
        column_config=topic_config,
        num_rows="dynamic",
        key="topic_grid",
        use_container_width=True
    )

    # ---------------------------------------------------------
    # STEP 3: THE "MAGIC BUTTON"
    # ---------------------------------------------------------
    st.markdown("---")
    if st.button("✨ Generate My Plan", type="primary"):
        
        # 1. Clean the Data
        # Convert the Pandas Grid (DataFrame) into a simple Python List
        slots_list = edited_schedule.to_dict("records")
        topics_raw = edited_topics.to_dict("records")
        
        # 2. Translate "Words" to "Numbers" for the algorithm
        # We filter out empty rows just in case
        valid_topics = []
        for t in topics_raw:
            if t['Topic Name']: # Only process if name exists
                # Use our Translator class to get the math numbers
                d_score = Translator.get_difficulty_score(t['Difficulty'])
                s_score = Translator.get_stability_score(t['Confidence'])
                
                valid_topics.append({
                    "Topic Name": t['Topic Name'],
                    "difficulty_score": d_score,
                    "stability_score": s_score
                })

        # 3. Run the Optimizer
        if not valid_topics or not slots_list:
            st.error("Please add at least one Time Slot and one Topic!")
        else:
            final_plan, leftovers = optimize_schedule(valid_topics, slots_list)
            
            # 4. Display Results
            st.success(f"Optimized {len(final_plan)} study sessions!")
            
            # Show the main table
            st.dataframe(pd.DataFrame(final_plan), use_container_width=True)
            
            # Show leftovers (if any)
            if leftovers:
                st.warning("⚠️ Not enough time for these topics:")
                for miss in leftovers:
                    st.write(f"- {miss['Topic Name']} (Urgency: High)")

if __name__ == "__main__":
    main()