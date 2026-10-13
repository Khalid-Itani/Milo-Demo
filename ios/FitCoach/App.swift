import SwiftUI

@main
struct FitCoachApp: App {
    @State private var store = AppStore()
    var body: some Scene {
        WindowGroup {
            RootView().environment(store)
        }
    }
}

enum Tab: Hashable { case today, coach, train, eat, goals }

struct RootView: View {
    @Environment(AppStore.self) private var store

    var body: some View {
        @Bindable var store = store
        TabView(selection: $store.selectedTab) {
            TodayView().tabItem { Label("Today", systemImage: "square.grid.2x2") }.tag(Tab.today)
            CoachView().tabItem { Label("Coach", systemImage: "bubble.left") }.tag(Tab.coach)
            TrainView().tabItem { Label("Train", systemImage: "dumbbell") }.tag(Tab.train)
            EatView().tabItem { Label("Eat", systemImage: "fork.knife") }.tag(Tab.eat)
            GoalsView().tabItem { Label("Goals", systemImage: "target") }.tag(Tab.goals)
        }
        .tint(Theme.ink)
        .sheet(isPresented: $store.showOnboarding) { OnboardingView() }
        .task { await store.loadProfile() }
        .alert("Something went wrong", isPresented: Binding(get: { store.error != nil }, set: { if !$0 { store.error = nil } })) {
            Button("OK") { store.error = nil }
        } message: { Text(store.error ?? "") }
    }
}

/// One store for every tab. Each tab calls its refresh on appear; mutations refresh what they touch.
@Observable @MainActor
final class AppStore {
    // Debug: launch with `-startTab eat` (or coach/train/goals) to open on that tab, e.g. for screenshots.
    var selectedTab: Tab = ["coach": .coach, "train": .train, "eat": .eat, "goals": .goals][
        UserDefaults.standard.string(forKey: "startTab") ?? ""] ?? .today
    var coachDraft = ""          // prefilled by "Adjust", "New goal", etc.
    var trainPath: [Int] = []    // workout ids pushed in the Train tab
    var showOnboarding = false
    var error: String?

    var profile: User?
    var today: Today?
    var workouts: [Workout] = []
    var food: FoodDay?
    var foodDate = Dates.todayString      // day shown on Eat
    var foodWeek: [FoodDay] = []          // last 7 days, oldest first
    var dietPlans: [DietPlan] = []
    var goals: [Goal] = []
    var scans: [Scan] = []
    var chat: [ChatMessage] = []
    var isSending = false
    var chatStatus: String?   // e.g. "Logging food…" while Milo runs a tool
    var undoneFoodIds: Set<Int> = []

    private let api = APIClient.shared

    private func run(_ work: () async throws -> Void) async {
        do { try await work() } catch is CancellationError {} catch let e as URLError where e.code == .cancelled {} catch {
            self.error = error.localizedDescription
        }
    }

    // MARK: Loads

    func loadProfile() async {
        await run {
            let user: User = try await api.get("/profile")
            profile = user
            showOnboarding = user.heightCm == nil
        }
    }
    func refreshToday() async { await run { today = try await api.get("/today") } }
    func refreshWorkouts() async { await run { workouts = try await api.get("/workouts") } }
    func refreshFood() async { await run { food = try await api.get("/food?date=\(foodDate)") } }
    func refreshDietPlans() async { await run { dietPlans = try await api.get("/diet-plans") } }

    func showFoodDay(_ day: String) async {
        foodDate = day
        await refreshFood()
    }

    func refreshFoodWeek() async {
        let days = (0..<7).reversed().map { Dates.day.string(from: Calendar.current.date(byAdding: .day, value: -$0, to: Date())!) }
        let api = api
        await run {
            foodWeek = try await withThrowingTaskGroup(of: (Int, FoodDay).self) { group in
                for (i, d) in days.enumerated() { group.addTask { (i, try await api.get("/food?date=\(d)")) } }
                var out: [(Int, FoodDay)] = []
                for try await r in group { out.append(r) }
                return out.sorted { $0.0 < $1.0 }.map(\.1)
            }
        }
    }
    func refreshGoals() async { await run { goals = try await api.get("/goals") } }
    func refreshScans() async { await run { scans = try await api.get("/scans") } }
    /// Current conversation (nil = the original one). "New chat" starts a fresh id; old messages stay in the DB.
    var conversationId: String? = UserDefaults.standard.string(forKey: "conversationId")

    func loadChat() async {
        guard !isSending else { return } // don't clobber the optimistic message mid-request
        await run { chat = try await api.get("/chat/history" + (conversationId.map { "?conversation_id=\($0)" } ?? "")) }
    }

    func newChat() {
        conversationId = UUID().uuidString
        UserDefaults.standard.set(conversationId, forKey: "conversationId")
        chat = []
    }

    // MARK: Profile

    func saveProfile(_ input: ProfileInput) async -> User? {
        var saved: User?
        await run {
            saved = try await api.post("/profile", input)
            profile = saved
        }
        await refreshToday()
        return saved
    }

    // MARK: Body scan (Visualize AI)

    func visualizeSessionToken() async throws -> String {
        let session: VisualizeSession = try await api.post("/visualize/session")
        return session.sessionToken
    }

    func setScanAISharing(_ on: Bool) async {
        await run { profile = try await api.post("/profile", ConsentInput(scanAiSharingConsent: on)) }
    }

    func allowScanStorage() async {
        await run { profile = try await api.post("/profile", ConsentInput(scanStorageConsent: true)) }
    }

    func saveBodyScan(metrics: [ScanMetric]) async {
        await run { let _: Scan = try await api.post("/scans", ScanInput(metrics: metrics)) }
        await refreshToday()
    }

    // MARK: Coach

    func send(_ text: String, photo: UIImage? = nil) async {
        var message = text.trimmingCharacters(in: .whitespacesAndNewlines)
        if photo != nil { message = "[Photo] " + (message.isEmpty ? "Log what I ate" : message) }
        guard !message.isEmpty, !isSending else { return }
        chat.append(ChatMessage(id: -(chat.count + 1), role: "user", content: message, cards: []))
        isSending = true
        defer { isSending = false }
        let input = ChatInput(message: message, imageJpegBase64: photo.map(Self.jpegBase64), conversationId: conversationId)
        if Config.useMock {
            await run {
                let reply: ChatReply = try await api.post("/chat", input)
                chat.append(ChatMessage(id: -(chat.count + 1), role: "assistant", content: reply.reply, cards: reply.cards))
            }
        } else {
            // Stream Milo's reply into a live message as it's written.
            chat.append(ChatMessage(id: -(chat.count + 1), role: "assistant", content: "", cards: []))
            let i = chat.count - 1
            await run {
                for try await event in api.chatStream(input) {
                    switch event.type {
                    case "text": chatStatus = nil; chat[i].content += event.text ?? ""
                    case "status": chat[i].content = ""; chatStatus = event.text
                    case "done": chat[i].content = event.reply ?? ""; chat[i].cards = event.cards ?? []
                    case "error": throw APIError(status: 500, body: event.detail ?? "chat_failed")
                    default: break
                    }
                }
            }
            chatStatus = nil
            if chat[i].content.isEmpty && (chat[i].cards ?? []).isEmpty { chat.remove(at: i) }
        }
        // Tools may have touched any tab's data.
        async let a: () = refreshToday()
        async let b: () = refreshFood()
        async let c: () = refreshWorkouts()
        async let d: () = refreshGoals()
        _ = await (a, b, c, d)
    }

    /// Meal photos are shrunk to ~1568 px (Claude's sweet spot) and sent as JPEG.
    static func jpegBase64(_ image: UIImage) -> String {
        let scale = min(1, 1568 / max(image.size.width, image.size.height))
        let size = CGSize(width: image.size.width * scale, height: image.size.height * scale)
        let resized = UIGraphicsImageRenderer(size: size).image { _ in image.draw(in: CGRect(origin: .zero, size: size)) }
        return resized.jpegData(compressionQuality: 0.7)?.base64EncodedString() ?? ""
    }

    func openCoach(prefill: String = "") {
        coachDraft = prefill
        selectedTab = .coach
    }

    // MARK: Train

    func startWorkout(_ id: Int) async {
        await run { let _: Workout = try await api.post("/workouts/\(id)/start") }
        await refreshWorkouts()
    }

    func finishWorkout(_ id: Int) async {
        await run { let _: Workout = try await api.post("/workouts/\(id)/finish") }
        await refreshWorkouts()
        await refreshToday()
    }

    func addSet(exerciseId: Int, kg: Double, reps: Int) async {
        await run { let _: WorkoutSet = try await api.post("/exercises/\(exerciseId)/sets", NewSet(kg: kg, reps: reps)) }
        await refreshWorkouts()
    }

    func updateSet(_ id: Int, _ patch: SetPatch) async {
        await run { let _: WorkoutSet = try await api.patch("/sets/\(id)", patch) }
        await refreshWorkouts()
    }

    // MARK: Eat

    func logFood(_ food: NewFood) async {
        await run { let _: FoodLog = try await api.post("/food", food) }
        await refreshFood()
        await refreshToday()
        await refreshFoodWeek()
    }

    func deleteFood(_ id: Int) async {
        await run {
            try await api.delete("/food/\(id)")
            undoneFoodIds.insert(id)
        }
        await refreshFood()
        await refreshToday()
    }
}
