import Foundation

// Exact shapes from SPEC §2.2. Keys are snake_case on the wire (convertFromSnakeCase).

struct User: Codable {
    var id: Int
    var name: String
    var heightCm: Double?
    var weightKg: Double?
    var age: Int?
    var sex: String?
    var kcalTarget: Double?
    var proteinG: Double?
    var carbsG: Double?
    var fatG: Double?
    var bmi: Double?
    var bmiCategory: String?
    var scanStorageConsent: Bool?
    var scanAiSharingConsent: Bool?
}

struct ProfileInput: Encodable {
    var heightCm: Double
    var weightKg: Double
    var age: Int
    var sex: String
}

struct Today: Codable {
    struct Kcal: Codable { var target: Double; var eaten: Double; var left: Double }
    struct Macro: Codable { var eaten: Double; var target: Double }
    struct Macros: Codable { var protein: Macro; var carbs: Macro; var fat: Macro }
    struct BMI: Codable { var value: Double?; var category: String? }
    struct Week: Codable { var sessionsDone: Int; var sessionsTarget: Int; var days: [Bool] }
    struct NextWorkout: Codable { var id: Int; var name: String; var exerciseCount: Int; var estMinutes: Int }

    var date: String
    var kcal: Kcal
    var macros: Macros
    var bmi: BMI?
    var weightKg: Double?
    var latestScan: Scan?
    var week: Week
    var nextWorkout: NextWorkout?
    var coachTip: String
}

struct WorkoutSet: Codable, Identifiable {
    var id: Int
    var exerciseId: Int
    var setIndex: Int
    var kg: Double
    var reps: Int
    var isWarmup: Bool
    var done: Bool
}

struct Exercise: Codable, Identifiable {
    var id: Int
    var workoutId: Int
    var name: String
    var position: Int
    var targetSets: Int
    var targetReps: Int
    var targetKg: Double?
    var sets: [WorkoutSet] = []
}

struct Workout: Codable, Identifiable {
    var id: Int
    var name: String
    var dayLabel: String
    var notes: String?
    var status: String // planned / active / done
    var startedAt: String?
    var finishedAt: String?
    var planId: Int?
    var exercises: [Exercise] = []
}

struct NewSet: Encodable { var kg: Double; var reps: Int; var isWarmup = false }
struct SetPatch: Encodable { var kg: Double?; var reps: Int?; var done: Bool? }

struct FoodLog: Codable, Identifiable {
    var id: Int
    var date: String
    var meal: String
    var name: String
    var quantity: String
    var kcal: Double
    var proteinG: Double
    var carbsG: Double
    var fatG: Double
    var source: String
}

struct NewFood: Encodable {
    var date: String
    var meal: String
    var name: String
    var quantity: String
    var kcal: Double
    var proteinG: Double
    var carbsG: Double
    var fatG: Double
    var source: String
}

struct Macros: Codable { var kcal: Double; var proteinG: Double; var carbsG: Double; var fatG: Double }

struct FoodDay: Codable {
    var date: String
    var meals: [String: [FoodLog]]
    var totals: Macros
}

struct DietMeal: Codable { var meal: String; var name: String; var kcal: Double; var proteinG: Double; var carbsG: Double; var fatG: Double }

struct DietPlan: Codable, Identifiable {
    var id: Int
    var name: String
    var kcal: Double
    var meals: [DietMeal]
    var createdAt: String?
}

struct Goal: Codable, Identifiable {
    var id: Int
    var title: String
    var kind: String // strength / body / nutrition / habit
    var unit: String
    var startValue: Double
    var currentValue: Double
    var targetValue: Double
    var deadline: String?
    var status: String // active / completed

    var progress: Double {
        let span = targetValue - startValue
        return span == 0 ? (currentValue >= targetValue ? 1 : 0) : min(max((currentValue - startValue) / span, 0), 1)
    }
}

struct ChatMessage: Codable, Identifiable {
    var id: Int
    var role: String
    var content: String
    var cards: [Card]?
    var createdAt: String?
}

struct ChatReply: Codable { var reply: String; var cards: [Card] }
/// One NDJSON line from POST /chat/stream.
struct ChatEvent: Decodable { var type: String; var text: String?; var reply: String?; var cards: [Card]?; var detail: String? }
struct ChatInput: Encodable { var message: String; var imageJpegBase64: String? = nil; var conversationId: String? = nil }
struct OK: Codable { var ok: Bool }
struct VisualizeSession: Codable { var sessionToken: String; var expiresAt: String }
struct ScanMetric: Codable { var identifier: String; var value: Double; var unit: String }
struct Scan: Codable { var id: Int; var capturedAt: String; var metrics: [ScanMetric] }
struct ScanInput: Encodable {
    var clientScanId = UUID().uuidString
    var capturedAt = ISO8601DateFormatter().string(from: Date())
    var source = "visualize_sdk"
    var metrics: [ScanMetric]
}
struct ConsentInput: Encodable { var scanStorageConsent: Bool? = nil; var scanAiSharingConsent: Bool? = nil }

// MARK: Cards

struct PlanCard: Codable {
    struct Day: Codable { var id: Int?; var dayLabel: String; var name: String; var summary: String }
    var planName: String
    var weeks: Int?
    var workouts: [Day]
}
struct FoodCard: Codable { var id: Int; var meal: String; var name: String; var kcal: Double; var proteinG: Double; var carbsG: Double; var fatG: Double }
struct GoalCard: Codable { var id: Int; var title: String; var targetValue: Double; var unit: String; var deadline: String? }
struct SetCard: Codable { var exercise: String; var kg: Double; var reps: Int }

enum Card: Codable {
    case workoutPlan(PlanCard)
    case foodLogged(FoodCard)
    case dietPlan(DietPlan)
    case goalSet(GoalCard)
    case setLogged(SetCard)
    case unknown(String)

    private enum Keys: String, CodingKey { case type, data }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: Keys.self)
        let type = try c.decode(String.self, forKey: .type)
        // A malformed card should never sink the whole chat response.
        do {
            switch type {
            case "workout_plan": self = .workoutPlan(try c.decode(PlanCard.self, forKey: .data))
            case "food_logged": self = .foodLogged(try c.decode(FoodCard.self, forKey: .data))
            case "diet_plan": self = .dietPlan(try c.decode(DietPlan.self, forKey: .data))
            case "goal_set": self = .goalSet(try c.decode(GoalCard.self, forKey: .data))
            case "set_logged": self = .setLogged(try c.decode(SetCard.self, forKey: .data))
            default: self = .unknown(type)
            }
        } catch {
            self = .unknown(type)
        }
    }

    func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: Keys.self)
        switch self {
        case .workoutPlan(let d): try c.encode("workout_plan", forKey: .type); try c.encode(d, forKey: .data)
        case .foodLogged(let d): try c.encode("food_logged", forKey: .type); try c.encode(d, forKey: .data)
        case .dietPlan(let d): try c.encode("diet_plan", forKey: .type); try c.encode(d, forKey: .data)
        case .goalSet(let d): try c.encode("goal_set", forKey: .type); try c.encode(d, forKey: .data)
        case .setLogged(let d): try c.encode("set_logged", forKey: .type); try c.encode(d, forKey: .data)
        case .unknown(let t): try c.encode(t, forKey: .type)
        }
    }
}

// MARK: Dates

enum Dates {
    static let day: DateFormatter = { let f = DateFormatter(); f.dateFormat = "yyyy-MM-dd"; f.locale = Locale(identifier: "en_US_POSIX"); return f }()

    /// Python isoformat: "2026-10-03T14:02:11.123456", with or without fraction / offset. Naive = local time.
    static func parse(_ s: String?) -> Date? {
        guard let s else { return nil }
        let iso = ISO8601DateFormatter()
        iso.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        if let d = iso.date(from: s) { return d }
        iso.formatOptions = [.withInternetDateTime]
        if let d = iso.date(from: s) { return d }
        let f = DateFormatter()
        f.locale = Locale(identifier: "en_US_POSIX")
        let base = String(s.prefix(19)) // drop fractional seconds
        f.dateFormat = "yyyy-MM-dd'T'HH:mm:ss"
        return f.date(from: base)
    }

    static func format(_ ymd: String?, _ pattern: String) -> String {
        guard let ymd, let d = day.date(from: ymd) else { return "" }
        let f = DateFormatter(); f.dateFormat = pattern
        return f.string(from: d)
    }

    static var todayString: String { day.string(from: Date()) }
}
