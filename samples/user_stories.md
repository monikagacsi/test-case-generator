# User Stories for Test Case Generation

This document contains a diverse set of 15 user stories spanning multiple domains (Authentication, E-Commerce, Banking/Fintech, Social Media, Healthcare, Travel, and Productivity). They are designed for practicing functional, boundary, negative, and edge-case test design.

---

## Authentication & Account Security (Login)

### 1. Account Lockout After Failed Login Attempts
* **As a** system security administrator,
* **I want to** temporarily lock a user account after a specific number of consecutive failed login attempts,
* **So that** I can protect user accounts from brute-force password guessing attacks.
* **Acceptance Criteria:**
  * The account locks automatically after 5 consecutive incorrect password entries.
  * A locked account displays a message instructing the user to check their email to unlock it or wait 30 minutes.
  * Successful login resets the failed attempt counter to zero.

### 2. Social Login (Google / Apple Sign-In)
* **As a** new visitor,
* **I want to** sign up and log in using my existing Google or Apple credentials,
* **So that** I don't have to remember a new password for this platform.
* **Acceptance Criteria:**
  * Clicking "Continue with Google" opens the OAuth consent screen.
  * Upon successful authorization, a new account is automatically created using the verified email from the provider.
  * If the email is already registered via standard email/password, the accounts are linked securely.

---

## E-Commerce & Retail

### 3. Multi-Currency Selector and Conversion
* **As an** international shopper,
* **I want to** change the store currency (e.g., USD, EUR, JPY),
* **So that** I can view product prices in my local currency.
* **Acceptance Criteria:**
  * Changing the currency updates all product prices across the catalog instantly using the latest exchange rates.
  * The selected currency persists throughout the browsing and checkout session.
  * The final payment summary displays the total in the selected currency alongside the base currency.

### 4. Cart Persistence Across Devices
* **As a** logged-in user,
* **I want to** see the items I added to my shopping cart on my mobile phone when I log in on my laptop,
* **So that** I can seamlessly continue my shopping experience.
* **Acceptance Criteria:**
  * Cart items sync in real-time across active sessions for the same user account.
  * If an item becomes out of stock while in the cart, a warning flag is displayed next to the product.

### 5. Product Review and Star Rating Submission
* **As a** verified purchaser,
* **I want to** submit a star rating (1–5) and a written review for a delivered product,
* **So that** I can share my feedback with other potential buyers.
* **Acceptance Criteria:**
  * Only users with a "Delivered" order status for that specific product can submit a review.
  * Reviews must be between 10 and 500 characters long.
  * Submitting a review immediately updates the product's average rating score.

---

## Banking & Fintech

### 6. Deposit Funds via Linked Debit Card
* **As a** bank account holder,
* **I want to** deposit money into my checking account using a saved debit card,
* **So that** I can fund my balance instantly.
* **Acceptance Criteria:**
  * The minimum deposit amount is $10.00 and the maximum single deposit is $1,000.00.
  * Transactions failing 3D Secure verification are rejected with a clear error prompt.
  * Successfully processed funds reflect in the available balance immediately.

### 7. Setting a Savings Goal with Automatic Round-Ups
* **As a** user trying to save money,
* **I want to** enable "spare change" round-ups on my debit card purchases,
* **So that** the difference is automatically transferred to my savings pot.
* **Acceptance Criteria:**
  * Every card transaction amount is rounded up to the nearest whole dollar (e.g., a $4.20 purchase rounds up to $5.00, putting $0.80 into savings).
  * Round-ups are transferred in a batch at the end of each business day.
  * Users can pause or toggle the round-up feature at any time.

---

## Social Media & Content Sharing

### 8. Direct Messaging with Read Receipts
* **As a** chat user,
* **I want to** send direct messages to another user and see when they have read them,
* **So that** I know if my message has been seen.
* **Acceptance Criteria:**
  * Messages support text and emojis up to 1,000 characters.
  * A single checkmark indicates delivery, and a double checkmark (or color change) indicates the message has been read.
  * Users can delete their sent messages for both participants within 10 minutes of sending.

### 9. Hashtag and Keyword Feed Search
* **As a** platform explorer,
* **I want to** search for posts containing a specific hashtag (e.g., `#TechTrends`),
* **So that** I can discover relevant content and discussions.
* **Acceptance Criteria:**
  * Search results display posts in reverse-chronological order.
  * Special characters and spaces in hashtags are handled gracefully (e.g., stripping spaces or matching exact tags).
  * If no posts match the hashtag, a friendly empty-state illustration and message are shown.

---

## Healthcare & Appointments

### 10. Doctor Appointment Booking
* **As a** patient,
* **I want to** select an available time slot on a doctor's calendar and book an appointment,
* **So that** I can receive a medical consultation.
* **Acceptance Criteria:**
  * Users can only select slots marked as "Available" in the doctor's schedule.
  * Double-booking the same slot is prevented even if two users click "Book" at the exact same millisecond.
  * A calendar invite (.ics file) and confirmation email are sent upon successful booking.

### 11. Prescription Refill Request
* **As a** patient managing a chronic condition,
* **I want to** request a refill for my active prescription through the app,
* **So that** I can pick it up at my preferred pharmacy without calling the clinic.
* **Acceptance Criteria:**
  * Only prescriptions with remaining authorized refills can be selected.
  * Users must select their preferred local pharmacy from a dropdown list before submitting.
  * Status updates (e.g., "Pending Doctor Approval", "Sent to Pharmacy") are tracked in real-time.

---

## Travel & Booking

### 12. Hotel Room Filtering and Sorting
* **As a** traveler looking for accommodation,
* **I want to** filter hotel search results by price range, star rating, and amenities (e.g., Wi-Fi, Pool),
* **So that** I can find a hotel that fits my preferences.
* **Acceptance Criteria:**
  * Multiple filters can be applied simultaneously (additive filtering).
  * Results can be sorted by "Price: Low to High", "Price: High to Low", or "Guest Rating".
  * If filters yield zero results, a "Clear Filters" button is displayed.

### 13. Flight Seat Selection Map
* **As a** passenger checking in for a flight,
* **I want to** view an interactive seat map and choose my preferred seat (Window, Aisle, Extra Legroom),
* **So that** I can sit comfortably during the journey.
* **Acceptance Criteria:**
  * Taken seats are grayed out and unselectable, available standard seats are white, and premium/extra legroom seats display an extra fee.
  * Users cannot finalize seat selection without confirming.
  * Group bookings allow selecting adjacent seats if available.

---

## Productivity & SaaS

### 14. Kanban Task Card Drag-and-Drop Status Update
* **As a** project manager,
* **I want to** drag and drop a task card from the "In Progress" column to the "Done" column on a Kanban board,
* **So that** I can update its status quickly.
* **Acceptance Criteria:**
  * Dropping a card into a new column updates its state instantly in the database.
  * Real-time updates reflect the change for other team members viewing the same board without requiring a page refresh.
  * Moving a task to "Done" automatically records the completion timestamp.

### 15. Recurring Task Automation
* **As a** team lead,
* **I want to** set a task to repeat every Monday,
* **So that** routine weekly chores are automatically recreated without manual entry.
* **Acceptance Criteria:**
  * Users can define custom recurrence rules (Daily, Weekly, Monthly, Yearly).
  * A new task instance is generated automatically at 00:00 local time on the scheduled recurrence day.
  * Deleting a recurring task series provides an option to delete only the current instance or all future instances.