import PhotosUI
import SwiftUI

struct CoachView: View {
    @Environment(AppStore.self) private var store
    @FocusState private var focused: Bool
    @State private var photoItem: PhotosPickerItem?
    @State private var photo: UIImage?
    @State private var confirmNewChat = false
    private let suggestions = ["Plan tonight’s dinner", "Swap an exercise", "How was my week?"]

    var body: some View {
        @Bindable var store = store
        VStack(spacing: 0) {
            HStack {
                if focused {
                    // The keyboard hides the tab bar; this closes it so you can navigate away.
                    Button { focused = false } label: {
                        Label("Back", systemImage: "chevron.left").font(Theme.text(15, .semibold))
                            .frame(minHeight: 44)
                    }
                    .accessibilityLabel("Close keyboard")
                }
                ScreenTitle(title: "Coach")
                Spacer()
                // ponytail: past chats / new chat are visual only in the MVP.
                Button {} label: { Image(systemName: "clock.arrow.circlepath").frame(width: 44, height: 44) }
                    .accessibilityLabel("Past chats")
                Button { confirmNewChat = true } label: { Image(systemName: "square.and.pencil").frame(width: 44, height: 44) }
                    .accessibilityLabel("New chat")
                    .disabled(store.isSending)
                    .confirmationDialog("Start a new chat?", isPresented: $confirmNewChat, titleVisibility: .visible) {
                        Button("New chat") { store.newChat() }
                    } message: {
                        Text("Clears this conversation. Your logged food, workouts, goals and scans are kept.")
                    }
            }
            .font(.system(size: 18))
            .foregroundStyle(Theme.ink)
            .padding(.leading, 20).padding(.trailing, 12).padding(.top, 8)

            ScrollViewReader { proxy in
                ScrollView {
                    LazyVStack(alignment: .leading, spacing: 14) {
                        ForEach(store.chat) { MessageView(message: $0) }
                        if let status = store.chatStatus {
                            Label(status, systemImage: "sparkles").font(Theme.text(13.5, .medium)).foregroundStyle(Theme.secondary)
                                .symbolEffect(.pulse)
                        } else if store.isSending && (store.chat.last?.content.isEmpty ?? true) {
                            Image(systemName: "ellipsis")
                                .font(.system(size: 22, weight: .bold)).foregroundStyle(Theme.secondary)
                                .symbolEffect(.variableColor.iterative)
                                .accessibilityLabel("Milo is typing")
                        }
                        Color.clear.frame(height: 1).id("bottom")
                    }
                    .padding(.horizontal, 16).padding(.vertical, 12)
                }
                .scrollDismissesKeyboard(.interactively)
                .onChange(of: store.chat.count) { withAnimation { proxy.scrollTo("bottom") } }
                .onChange(of: store.isSending) { withAnimation { proxy.scrollTo("bottom") } }
                .onChange(of: store.chat.last?.content) { proxy.scrollTo("bottom") }
                .onAppear { proxy.scrollTo("bottom") }
            }

            inputArea(draft: $store.coachDraft)
        }
        .screen()
        .task { await store.loadChat() }
        .onAppear { if !store.coachDraft.isEmpty { focused = true } }
        .onChange(of: store.coachDraft) { _, new in if !new.isEmpty && !focused { focused = true } }
    }

    private func inputArea(draft: Binding<String>) -> some View {
        VStack(spacing: 10) {
            ScrollView(.horizontal, showsIndicators: false) {
                HStack(spacing: 8) {
                    ForEach(suggestions, id: \.self) { s in
                        Button { Task { await store.send(s) } } label: {
                            Text(s).font(Theme.text(13, .medium)).foregroundStyle(Theme.ink)
                                .padding(.horizontal, 14).frame(height: 36)
                                .background(.white, in: Capsule())
                                .overlay(Capsule().stroke(Theme.border))
                        }
                        .disabled(store.isSending)
                    }
                }
                .padding(.horizontal, 12)
            }
            if let photo {
                HStack {
                    Image(uiImage: photo).resizable().scaledToFill()
                        .frame(width: 64, height: 64).clipShape(RoundedRectangle(cornerRadius: 12, style: .continuous))
                        .overlay(alignment: .topTrailing) {
                            Button { self.photo = nil; photoItem = nil } label: {
                                Image(systemName: "xmark").font(.system(size: 10, weight: .bold)).foregroundStyle(.white)
                                    .frame(width: 22, height: 22).background(Theme.ink, in: Circle())
                            }
                            .offset(x: 6, y: -6)
                            .accessibilityLabel("Remove photo")
                        }
                    Text("Milo will log what's in the photo. Add a note if you like.")
                        .font(Theme.text(12.5)).foregroundStyle(Theme.secondary)
                    Spacer()
                }
                .padding(.horizontal, 18)
            }
            HStack(spacing: 4) {
                PhotosPicker(selection: $photoItem, matching: .images) {
                    Image(systemName: "plus").frame(width: 40, height: 40).background(Theme.trainTrack, in: Circle())
                }
                .accessibilityLabel("Add a meal photo")
                .onChange(of: photoItem) {
                    Task {
                        if let data = try? await photoItem?.loadTransferable(type: Data.self) { photo = UIImage(data: data) }
                    }
                }
                TextField("Ask, log a meal, plan a workout", text: draft, axis: .vertical)
                    .font(Theme.text(15)).lineLimit(1...4)
                    .padding(.horizontal, 6)
                    .focused($focused)
                    .submitLabel(.send)
                    .onSubmit(submit)
                Button {} label: { Image(systemName: "mic").foregroundStyle(Theme.secondary).frame(width: 40, height: 40) }
                    .accessibilityLabel("Dictate")
                Button(action: submit) {
                    Image(systemName: "arrow.up").font(.system(size: 16, weight: .semibold)).foregroundStyle(.white)
                        .frame(width: 40, height: 40).background(Theme.ink, in: Circle())
                }
                .accessibilityLabel("Send")
                .disabled(store.isSending || (photo == nil && draft.wrappedValue.trimmingCharacters(in: .whitespaces).isEmpty))
            }
            .foregroundStyle(Theme.ink)
            .padding(6)
            .background(.white, in: RoundedRectangle(cornerRadius: 26, style: .continuous))
            .overlay(RoundedRectangle(cornerRadius: 26, style: .continuous).stroke(Theme.border))
            .shadow(color: Theme.ink.opacity(0.06), radius: 10, y: 6)
            .padding(.horizontal, 12)
        }
        .padding(.top, 10).padding(.bottom, 12)
        .background(Theme.bg)
    }

    private func submit() {
        let text = store.coachDraft, image = photo
        store.coachDraft = ""
        photo = nil
        photoItem = nil
        Task { await store.send(text, photo: image) }
    }
}

private struct MessageView: View {
    var message: ChatMessage

    var body: some View {
        if message.role == "user" {
            Text(message.content)
                .font(Theme.text(15)).foregroundStyle(.white).lineSpacing(3)
                .padding(.horizontal, 14).padding(.vertical, 11)
                .background(Theme.ink, in: UnevenRoundedRectangle(topLeadingRadius: 20, bottomLeadingRadius: 20, bottomTrailingRadius: 6, topTrailingRadius: 20, style: .continuous))
                .frame(maxWidth: 290, alignment: .trailing)
                .frame(maxWidth: .infinity, alignment: .trailing)
        } else {
            VStack(alignment: .leading, spacing: 10) {
                if !message.content.isEmpty {
                    // Milo is asked for plain text; render stray inline markdown, or strip it if parsing fails.
                    Text((try? AttributedString(markdown: message.content, options: .init(interpretedSyntax: .inlineOnlyPreservingWhitespace)))
                         ?? AttributedString(message.content.replacingOccurrences(of: "**", with: "")))
                        .font(Theme.text(15)).lineSpacing(4).foregroundStyle(Theme.ink)
                }
                ForEach(Array(grouped(message.cards ?? []).enumerated()), id: \.offset) { _, item in
                    switch item {
                    case .card(let card): CardView(card: card)
                    case .sets(let sets): SetsCardView(sets: sets)
                    }
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
        }
    }
}

// MARK: Cards

/// All set cards in one reply collapse into a single card with a row per exercise.
private enum DisplayCard { case card(Card), sets([SetCard]) }

private func grouped(_ cards: [Card]) -> [DisplayCard] {
    var out: [DisplayCard] = []
    var setsAt: Int?
    for card in cards {
        guard case .setLogged(let set) = card else { out.append(.card(card)); continue }
        if let i = setsAt, case .sets(var sets) = out[i] {
            sets.append(set)
            out[i] = .sets(sets)
        } else {
            setsAt = out.count
            out.append(.sets([set]))
        }
    }
    return out
}

private struct SetsCardView: View {
    var sets: [SetCard]

    /// Exercises in first-logged order, each with its sets.
    private var exercises: [(name: String, sets: [SetCard])] {
        var order: [String] = []
        var byName: [String: [SetCard]] = [:]
        for set in sets {
            if byName[set.exercise] == nil { order.append(set.exercise) }
            byName[set.exercise, default: []].append(set)
        }
        return order.map { ($0, byName[$0]!) }
    }

    private func summary(_ sets: [SetCard]) -> String {
        let first = sets[0]
        if sets.allSatisfy({ $0.kg == first.kg && $0.reps == first.reps }) {
            let weight = first.kg > 0 ? " @ \(fmt(first.kg)) kg" : ""
            return sets.count == 1 ? "\(first.reps) reps\(weight)" : "\(sets.count) × \(first.reps)\(weight)"
        }
        return sets.map { "\(fmt($0.kg))×\($0.reps)" }.joined(separator: " · ")
    }

    var body: some View {
        CardShell {
            VStack(alignment: .leading, spacing: 0) {
                HStack(spacing: 10) {
                    Image(systemName: "checkmark").font(.system(size: 14, weight: .bold)).foregroundStyle(Theme.ink)
                        .frame(width: 32, height: 32).background(Theme.train, in: RoundedRectangle(cornerRadius: 10))
                    VStack(alignment: .leading, spacing: 0) {
                        Text(sets.count == 1 ? "Set logged" : "\(sets.count) sets logged")
                            .font(Theme.text(12, .semibold)).foregroundStyle(Theme.secondary)
                        Text(exercises.count == 1 ? exercises[0].name : "\(exercises.count) exercises")
                            .font(Theme.text(14, .semibold))
                    }
                    Spacer()
                }
                .padding(.horizontal, 14).padding(.vertical, 12)
                ForEach(exercises, id: \.name) { ex in
                    Divider().overlay(Theme.hairline)
                    HStack(alignment: .firstTextBaseline) {
                        Text(ex.name).font(Theme.text(14))
                        Spacer(minLength: 12)
                        Text(summary(ex.sets)).font(Theme.mono(13)).multilineTextAlignment(.trailing)
                    }
                    .padding(.horizontal, 14).frame(minHeight: 40)
                }
            }
        }
    }
}

private struct CardView: View {
    var card: Card
    var body: some View {
        switch card {
        case .workoutPlan(let p): PlanCardView(plan: p)
        case .foodLogged(let f): FoodCardView(food: f)
        case .dietPlan(let d): DietCardView(plan: d)
        case .goalSet(let g):
            IconCard(icon: "target", tint: Theme.ink, bg: Theme.trainTrack, kicker: "Goal set", title: g.title,
                     trailing: "\(fmt(g.targetValue)) \(g.unit)",
                     detail: g.deadline.map { "by \(Dates.format($0, "MMM d, yyyy"))" })
        case .setLogged(let s):
            IconCard(icon: "checkmark", tint: Theme.ink, bg: Theme.train, kicker: "Set logged", title: s.exercise,
                     trailing: "\(fmt(s.kg)) × \(s.reps)", detail: nil)
        case .unknown: EmptyView()
        }
    }
}

private struct CardShell<Content: View>: View {
    @ViewBuilder var content: Content
    var body: some View {
        content
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(.white, in: RoundedRectangle(cornerRadius: 18, style: .continuous))
            .overlay(RoundedRectangle(cornerRadius: 18, style: .continuous).stroke(Color(hex: 0xE6E4DF)))
    }
}

private struct PlanCardView: View {
    @Environment(AppStore.self) private var store
    var plan: PlanCard

    var body: some View {
        CardShell {
            VStack(spacing: 0) {
                HStack {
                    Text(plan.planName).font(Theme.text(15, .semibold))
                    Spacer()
                    if let w = plan.weeks { Pill(text: "\(w) weeks") }
                }
                .padding(.horizontal, 14).padding(.top, 14).padding(.bottom, 10)
                ForEach(Array(plan.workouts.enumerated()), id: \.offset) { _, d in
                    Divider().overlay(Theme.hairline)
                    HStack(alignment: .top, spacing: 10) {
                        Text(d.dayLabel).font(Theme.mono(12)).foregroundStyle(Theme.secondary).frame(width: 40, alignment: .leading)
                        VStack(alignment: .leading, spacing: 1) {
                            Text(d.name).font(Theme.text(13.5, .semibold))
                            Text(d.summary).font(Theme.text(12.5)).foregroundStyle(Theme.secondary)
                        }
                        Spacer(minLength: 0)
                    }
                    .padding(.horizontal, 14).padding(.vertical, 9)
                }
                Divider().overlay(Theme.hairline)
                // The tool call already saved the plan; the button confirms it and then links to Train.
                SaveRow(save: "Save to Train", view: "View in Train",
                        open: { store.trainPath = []; store.selectedTab = .train },
                        secondary: ("Adjust", { store.coachDraft = "Adjust the plan: " }))
            }
        }
    }
}

private struct FoodCardView: View {
    @Environment(AppStore.self) private var store
    var food: FoodCard

    var body: some View {
        let undone = store.undoneFoodIds.contains(food.id)
        CardShell {
            VStack(alignment: .leading, spacing: 10) {
                HStack(spacing: 10) {
                    Image(systemName: undone ? "arrow.uturn.backward" : "checkmark")
                        .font(.system(size: 14, weight: .bold)).foregroundStyle(Theme.fuel)
                        .frame(width: 32, height: 32).background(Theme.fuelTint2, in: RoundedRectangle(cornerRadius: 10))
                    VStack(alignment: .leading, spacing: 0) {
                        Text(undone ? "Removed" : "Logged to \(food.meal.capitalized)").font(Theme.text(12, .semibold)).foregroundStyle(Theme.fuel)
                        Text(food.name).font(Theme.text(14, .semibold)).strikethrough(undone)
                    }
                    Spacer()
                    Text(fmt(food.kcal)).font(Theme.mono(14))
                }
                HStack(spacing: 6) {
                    Chip(text: "\(fmt(food.proteinG)) P")
                    Chip(text: "\(fmt(food.carbsG)) C")
                    Chip(text: "\(fmt(food.fatG)) F")
                    Spacer()
                    if !undone {
                        Button("Undo") { Task { await store.deleteFood(food.id) } }
                            .font(Theme.text(13, .semibold)).foregroundStyle(Theme.secondary)
                            .frame(minHeight: 44)
                    }
                }
            }
            .padding(.horizontal, 14).padding(.vertical, 12)
        }
    }
}

private struct DietCardView: View {
    @Environment(AppStore.self) private var store
    var plan: DietPlan
    var body: some View {
        CardShell {
            VStack(alignment: .leading, spacing: 0) {
                HStack {
                    Text(plan.name).font(Theme.text(15, .semibold))
                    Spacer()
                    Pill(text: "\(fmt(plan.kcal)) kcal", fg: Theme.fuelDeep, bg: Theme.fuelTint2)
                }
                .padding(.horizontal, 14).padding(.top, 14).padding(.bottom, 10)
                ForEach(Array(plan.meals.enumerated()), id: \.offset) { _, m in
                    Divider().overlay(Theme.hairline)
                    HStack(alignment: .top, spacing: 10) {
                        Text(m.meal.prefix(5).uppercased()).font(Theme.mono(11)).foregroundStyle(Theme.secondary).frame(width: 46, alignment: .leading)
                        VStack(alignment: .leading, spacing: 1) {
                            Text(m.name).font(Theme.text(13.5, .semibold))
                            Text("\(fmt(m.proteinG)) P · \(fmt(m.carbsG)) C · \(fmt(m.fatG)) F")
                                .font(Theme.mono(12, .regular)).foregroundStyle(Theme.secondary)
                        }
                        Spacer()
                        Text(fmt(m.kcal)).font(Theme.mono(13))
                    }
                    .padding(.horizontal, 14).padding(.vertical, 9)
                }
                Divider().overlay(Theme.hairline)
                SaveRow(save: "Save plan", view: "View in Eat",
                        open: { store.selectedTab = .eat },
                        secondary: ("Adjust", { store.coachDraft = "Adjust the diet plan: " }))
            }
        }
    }
}

/// "Save" confirms a plan the tool already saved: it flips to a checked "Saved" state and offers a link to view it.
private struct SaveRow: View {
    var save: String
    var view: String
    var open: () -> Void
    var secondary: (String, () -> Void)
    @State private var saved = false

    var body: some View {
        HStack(spacing: 8) {
            Button {
                withAnimation(.snappy) { saved = true }
                UINotificationFeedbackGenerator().notificationOccurred(.success)
            } label: {
                Label(saved ? "Saved" : save, systemImage: saved ? "checkmark.circle.fill" : "square.and.arrow.down")
            }
            .buttonStyle(BigButtonStyle(bg: saved ? Theme.fuel : Theme.ink))
            .disabled(saved)
            .accessibilityLabel(saved ? "Saved" : save)
            if saved {
                Button(view, action: open)
                    .buttonStyle(BigButtonStyle(bg: .white, fg: Theme.ink, border: Theme.border))
                    .transition(.opacity)
            } else {
                Button(secondary.0, action: secondary.1)
                    .buttonStyle(BigButtonStyle(bg: .white, fg: Theme.ink, border: Theme.border))
            }
        }
        .padding(.horizontal, 14).padding(.top, 12).padding(.bottom, 14)
    }
}

private struct IconCard: View {
    var icon: String
    var tint: Color
    var bg: Color
    var kicker: String
    var title: String
    var trailing: String
    var detail: String?

    var body: some View {
        CardShell {
            HStack(spacing: 10) {
                Image(systemName: icon).font(.system(size: 14, weight: .bold)).foregroundStyle(tint)
                    .frame(width: 32, height: 32).background(bg, in: RoundedRectangle(cornerRadius: 10))
                VStack(alignment: .leading, spacing: 0) {
                    Text(kicker).font(Theme.text(12, .semibold)).foregroundStyle(Theme.secondary)
                    Text(title).font(Theme.text(14, .semibold))
                    if let detail { Text(detail).font(Theme.text(12.5)).foregroundStyle(Theme.secondary) }
                }
                Spacer()
                Text(trailing).font(Theme.mono(14))
            }
            .padding(.horizontal, 14).padding(.vertical, 12)
        }
    }
}
