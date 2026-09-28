# Constrained-Path Scheduler 🎓

### Bridging the gap between "Perfect Math" and "Real Student Life"

![Status](https://img.shields.io/badge/Status-Phase%202:%20Prototype-orange) ![Python](https://img.shields.io/badge/Python-3.9+-blue) ![Tech](https://img.shields.io/badge/Tech-Streamlit%20|%20Pandas-red)

## 👋 The Motivation

> *"I have an exam in 3 days, a football match tonight, and I only have 20 minutes on the bus. What should I study?"*

We built this project because standard spaced-repetition tools (like Anki) are brilliant at math, but bad at **scheduling**. They tell you to review 500 cards today, regardless of whether you actually have the time. They optimize for *memory*, but they ignore *reality* [1].

**Constrained-Path Scheduler** is our attempt to fix this. We combined the **FSRS Memory Model** (to predict forgetting) with a **Bin-Packing Algorithm** (to manage time) to answer one simple question: *Given my crazy schedule, what is the single most urgent thing I should study right now?* [2]

---

## 🚀 What It Does

We moved away from asking students for abstract numbers ("Rate difficulty 1-10") and built a system that understands human inputs while doing the heavy mathematical lifting in the background [2].

*   **🗣️ The "Translator" Engine:** You don't need to know what a "retention rate" is. Just tell the app a topic is a *"Nightmare"* or that you *"Vaguely Recall"* it. Our code converts these qualitative feelings into precise quantitative weights ($D$ and $S$ parameters) [3].
*   **⚡ Urgency Scoring:** We don't just sort by "Hardest." We calculate an **Urgency Score** for every topic. A hard topic you know well is *less* urgent than an easy topic you are about to forget [3].
*   **🧩 Real-World Bin Packing:** Instead of demanding 2 hours of study time, the algorithm asks for your *available time slots* (e.g., "Morning Commute", "Lunch Break") and fits the most critical topics into those specific gaps [3].

---

## ⚙️ Under the Hood (The Algorithms)

We implemented a three-stage pipeline to transform user input into an optimal schedule:

### 1. The FSRS Logic (Simplified)
We adapted the **Free Spaced Repetition Scheduler** model to predict memory decay [4].
*   **Input:** User confidence level (Qualitative).
*   **Output:** Estimated Stability ($S$) in days.
*   *Academic Note:* We assume an exponential decay curve where $R = S \times (1 + \text{difficulty-factor})$ [4].

### 2. The Urgency Heuristic
To determine priority, we derived a dynamic scoring function [4]:

$$Urgency = \frac{\text{Difficulty Score}}{\max(\text{Stability}, 0.1)}$$

This ensures that **High Difficulty** combined with **Low Stability** always floats to the top of the priority queue [5].

### 3. The Solvers (Two Modes)

The project currently features two scheduling engines for performance comparison:

*   **Baseline (Current Prototype): The Greedy Bin-Packer**
    We treat the scheduling problem as a **Bin Packing Problem** [5].
    1.  **Bins:** The student's free time slots (sorted by duration).
    2.  **Items:** The study topics (sorted by Urgency).
    3.  **Process:** The algorithm iterates through the slots and attempts to fill them with the highest-urgency item that fits the time constraint, maximizing the "value" of every minute spent studying [5].

*   **Advanced (In Development): The A* Search Agent**
    A graph-search algorithm that explores multiple potential study paths. It uses a "Retention Deficit" heuristic ($h(n)$) to minimize the total time required to reach mastery across *all* topics, looking ahead to avoid dead-ends in the schedule.

---

## 🛠️ How to Run It

We built the interface using **Streamlit** so it feels like a modern app rather than a script [6].

**1. Clone the repo**
```bash
git clone https://github.com/YOUR-USERNAME/Constrained-Path-Scheduler.git
cd Constrained-Path-Scheduler
2. Install dependencies
pip install streamlit pandas
3. Run the App
streamlit run app.py

--------------------------------------------------------------------------------
📅 Roadmap & Progress
• [x] Phase 1: Foundation: Literature Review & FSRS Logic implementation.
• [x] Phase 2: Prototype: Streamlit UI with "Translator" Engine & Greedy Baseline.
• [ ] Phase 3: Algorithms: Implementation of A* Search for optimal pathfinding.
• [ ] Phase 4: Evaluation: Comparative analysis (Greedy vs. A*) on synthetic datasets.
