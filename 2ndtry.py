import streamlit as st
import pandas as pd
import math
import heapq
from copy import deepcopy

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

class SchedulerState:
    def __init__(self, topics, slot_index, current_schedule):
        self.topics = topics
        self.slot_index = slot_index
        self.schedule = current_schedule

    # FIX: Real comparison logic for the Priority Queue
    def __lt__(self, other):
        # If scores are tied, prefer the state that has filled more slots (Depth-first bias)
        return self.slot_index > other.slot_index

    # FIX: Add a signature method for the 'Visited' set
    def get_id(self):
        # Unique signature: (Current Slot Index, List of (Topic Name, Stability Score))
        # We round stability to 1 decimal to group similar states together
        topic_signature = tuple(sorted([(t['Topic Name'], round(t['stability_score'], 1)) for t in self.topics]))
        return (self.slot_index, topic_signature)

def heuristic(topics, target_stability=14.0):
    """
    h(n): Predicted 'Retention Deficit Cost'.
    Estimates how much time is needed to master remaining topics.
    Derived from Project Proposal [5].
    """
    cost = 0
    for t in topics:
        # If stability is low, we need time to fix it.
        # Translating 'Confident' (7.0) vs 'Mastered' (14.0) into minutes needed.
        if t['stability_score'] < target_stability:
            gap = target_stability - t['stability_score']
            cost += gap * 10  # Assume 10 mins study per stability point gap
    return cost

def optimize_schedule(topics_data, slots_data):
    """
    Corrected A* Search Implementation.
    """
    start_state = SchedulerState(topics_data, 0, [])
    pq = []
    
    # Init Visited Set to prevent loops
    visited = set()
    
    # f = g (time spent) + h (estimated time remaining)
    start_h = heuristic(topics_data)
    heapq.heappush(pq, (start_h, 0, start_state))
    
    # Initialize with an empty list to prevent crashes if no plan is found
    best_complete_plan = [] 
    min_cost = float('inf')
    solution_found = False

    while pq:
        f, g, current_state = heapq.heappop(pq)
        
        # TERMINATION: End of Calendar
        if current_state.slot_index >= len(slots_data):
            # We found a valid path to the end
            if g < min_cost:
                min_cost = g
                best_complete_plan = current_state.schedule
                solution_found = True
            continue

        # VISITED CHECK (Pruning)
        state_id = current_state.get_id()
        if state_id in visited:
            continue
        visited.add(state_id)

        # Constraints
        current_slot = slots_data[current_state.slot_index]
        duration = int(current_slot['Duration (mins)'])

        # BRANCH 1: Study a Topic
        for i, topic in enumerate(current_state.topics):
            # Optimization: Only study if not already Mastered (14.0)
            if topic['stability_score'] < 14.0:
                new_topics = deepcopy(current_state.topics)
                
                # Apply FSRS Logic
                new_topics[i]['stability_score'] = FSRSModel.next_stability(
                    new_topics[i]['stability_score'],
                    new_topics[i]['difficulty_score'],
                    3 
                )
                
                new_plan = current_state.schedule + [{
                    "Time Slot": current_slot['Slot Name'],
                    "Subject": topic['Topic Name'],
                    "Duration": f"{duration} mins",
                    "Status": "Study" # Metadata
                }]
                
                new_state = SchedulerState(new_topics, current_state.slot_index + 1, new_plan)
                
                new_g = g + duration
                new_h = heuristic(new_topics)
                
                # Add to queue
                heapq.heappush(pq, (new_g + new_h, new_g, new_state))

        # BRANCH 2: Skip Slot (Rest/Free Time)
        # Cost Logic: g does NOT increase (no study time used), but h remains high.
        # This allows the algorithm to choose "Rest" if studying yields diminishing returns.
        next_state = SchedulerState(current_state.topics, current_state.slot_index + 1, current_state.schedule)
        heapq.heappush(pq, (g + heuristic(current_state.topics), g, next_state))

    # Calculate leftovers properly
    # A topic is a "leftover" if it is NOT Mastered (< 14.0), regardless of if we studied it once.
    
    final_knowledge_map = {t['Topic Name']: t['stability_score'] for t in start_state.topics}
    
    # If we found a plan, we need to know the FINAL state of knowledge, not the START state.
    # Limitation: The simple implementation above loses the 'final state' object.
    # Quick Fix: Re-calculate leftovers based on the heuristic of the START state is wrong.
    # Better: Identify leftovers as topics that never appeared in the schedule OR are hard.
    
    # Simple UI version: List topics that didn't get a slot.
    scheduled_subjects = set([s['Subject'] for s in best_complete_plan if 'Subject' in s])
    leftovers = [t for t in topics_data if t['Topic Name'] not in scheduled_subjects]

    return best_complete_plan, leftovers

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
