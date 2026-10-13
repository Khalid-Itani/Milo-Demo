import SwiftUI

struct EatView: View {
    @Environment(AppStore.self) private var store
    @State private var text = ""
    @State private var logging = false
    @State private var expandedPlan: Int?
    private let meals = [("breakfast", "Breakfast"), ("lunch", "Lunch"), ("snack", "Snacks"), ("dinner", "Dinner")]

    private var isToday: Bool { store.foodDate == Dates.todayString }
    private var activePlan: DietPlan? { store.dietPlans.first }

    var body: some View {
        // List (not ScrollView) so meal rows get native swipe-to-delete.
        List {
            Group {
                header
                summary
                if let plan = activePlan { DietPlanCard(plan: plan, title: "Your plan", expanded: true) }
                weekCard
                if isToday { describeField }
                Text("FOOD LOG").font(Theme.text(11.5, .semibold)).tracking(0.6).foregroundStyle(Theme.secondary)
                    .padding(.top, 6).padding(.horizontal, 4)
            }
            .listRowInsets(EdgeInsets(top: 6, leading: 16, bottom: 6, trailing: 16))
            .listRowBackground(Color.clear)
            .listRowSeparator(.hidden)

            ForEach(meals, id: \.0) { key, title in
                mealSection(key: key, title: title, items: store.food?.meals[key] ?? [])
            }

            Section {
                plansHeader
                ForEach(store.dietPlans.dropFirst()) { plan in
                    DietPlanCard(plan: plan, title: nil, expanded: expandedPlan == plan.id) {
                        withAnimation { expandedPlan = expandedPlan == plan.id ? nil : plan.id }
                    }
                }
                if store.dietPlans.isEmpty {
                    DashedRow(text: "Ask Milo to create a diet plan") { store.openCoach(prefill: "Create a diet plan for me: ") }
                }
            }
            .listRowInsets(EdgeInsets(top: 6, leading: 16, bottom: 6, trailing: 16))
            .listRowBackground(Color.clear)
            .listRowSeparator(.hidden)
        }
        .listStyle(.insetGrouped)
        .listSectionSpacing(10)
        .scrollContentBackground(.hidden)
        .scrollDismissesKeyboard(.interactively)
        .screen()
        .refreshable { await refresh() }
        .task { await refresh() }
    }

    private func refresh() async {
        async let a: () = store.refreshFood()
        async let b: () = store.refreshToday()
        async let c: () = store.refreshFoodWeek()
        async let d: () = store.refreshDietPlans()
        _ = await (a, b, c, d)
    }

    // MARK: Header + day paging

    private func shift(_ days: Int) {
        guard let d = Dates.day.date(from: store.foodDate),
              let next = Calendar.current.date(byAdding: .day, value: days, to: d) else { return }
        Task { await store.showFoodDay(Dates.day.string(from: next)) }
    }

    private static func label(_ day: String) -> String {
        guard let d = Dates.day.date(from: day) else { return day }
        if Calendar.current.isDateInToday(d) { return "Today" }
        if Calendar.current.isDateInYesterday(d) { return "Yesterday" }
        return d.formatted(.dateTime.weekday(.abbreviated).month(.abbreviated).day())
    }

    private var header: some View {
        HStack {
            ScreenTitle(title: "Eat")
            Spacer()
            HStack(spacing: 2) {
                Button { shift(-1) } label: { Image(systemName: "chevron.left").frame(width: 40, height: 40) }
                    .accessibilityLabel("Previous day")
                Text(Self.label(store.foodDate)).font(Theme.text(14, .semibold)).padding(.horizontal, 4)
                    .frame(minWidth: 84)
                Button { shift(1) } label: { Image(systemName: "chevron.right").frame(width: 40, height: 40) }
                    .disabled(isToday)
                    .foregroundStyle(isToday ? Theme.fat : Theme.ink)
                    .accessibilityLabel("Next day")
            }
            .buttonStyle(.plain)
            .font(.system(size: 14, weight: .semibold))
            .padding(2)
            .background(.white, in: Capsule())
        }
        .padding(.horizontal, 4)
    }

    // MARK: Day summary (selected day's food vs saved targets)

    @ViewBuilder
    private var summary: some View {
        if let t = store.today, let food = store.food {
            let left = t.kcal.target - food.totals.kcal
            VStack(spacing: 14) {
                HStack(alignment: .bottom) {
                    stat(fmt(t.kcal.target), "Goal")
                    op("−")
                    stat(fmt(food.totals.kcal), "Food")
                    op("=")
                    stat(fmt(abs(left)), left < 0 ? "Over" : "Left", color: left < 0 ? Theme.train : Theme.fuel)
                }
                HStack(spacing: 12) {
                    macro("Protein", food.totals.proteinG, t.macros.protein.target, Theme.fuel)
                    macro("Carbs", food.totals.carbsG, t.macros.carbs.target, Theme.carbs)
                    macro("Fat", food.totals.fatG, t.macros.fat.target, Theme.fat)
                }
            }
            .card(22)
        }
    }

    private func stat(_ value: String, _ label: String, color: Color = Theme.ink) -> some View {
        VStack(spacing: 0) {
            Text(value).font(Theme.mono(19)).foregroundStyle(color)
            Text(label).font(Theme.text(11.5)).foregroundStyle(Theme.secondary)
        }
        .frame(maxWidth: .infinity)
    }

    private func op(_ s: String) -> some View {
        Text(s).font(Theme.mono(17, .regular)).foregroundStyle(Color(hex: 0x8A8F98)).frame(width: 16).padding(.bottom, 16)
    }

    private func macro(_ name: String, _ eaten: Double, _ target: Double, _ color: Color) -> some View {
        VStack(alignment: .leading, spacing: 5) {
            HStack {
                Text(name).font(Theme.text(12, .medium))
                Spacer()
                Text(fmt(eaten)).font(Theme.mono(12, .regular)).foregroundStyle(Theme.secondary)
            }
            Bar(progress: target > 0 ? eaten / target : 0, color: color, height: 5)
            Text("of \(fmt(target)) g").font(Theme.text(11)).foregroundStyle(Theme.secondary)
        }
        .frame(maxWidth: .infinity)
    }

    // MARK: Last 7 days

    private var weekCard: some View {
        let target = store.today?.kcal.target ?? 0
        let days = store.foodWeek
        let maxKcal = max(days.map(\.totals.kcal).max() ?? 0, target, 1)
        let logged = days.filter { $0.totals.kcal > 0 }
        let avg = logged.isEmpty ? 0 : logged.map(\.totals.kcal).reduce(0, +) / Double(logged.count)
        return VStack(alignment: .leading, spacing: 12) {
            HStack(alignment: .firstTextBaseline) {
                Text("Last 7 days").font(Theme.text(15, .semibold))
                Spacer()
                (Text(fmt(avg.rounded())).font(Theme.mono(13)) + Text(" kcal avg").font(Theme.text(12)).foregroundColor(Theme.secondary))
            }
            HStack(alignment: .bottom, spacing: 8) {
                ForEach(days, id: \.date) { day in
                    let selected = day.date == store.foodDate
                    let over = target > 0 && day.totals.kcal > target
                    Button { Task { await store.showFoodDay(day.date) } } label: {
                        VStack(spacing: 6) {
                            RoundedRectangle(cornerRadius: 6, style: .continuous)
                                .fill(over ? Theme.train : selected ? Theme.fuel : Theme.carbs)
                                .opacity(selected || over ? 1 : 0.55)
                                .frame(height: max(4, 84 * day.totals.kcal / maxKcal))
                            Text(Dates.day.date(from: day.date)?.formatted(.dateTime.weekday(.narrow)) ?? "")
                                .font(Theme.text(11.5, selected ? .bold : .regular))
                                .foregroundStyle(selected ? Theme.ink : Theme.secondary)
                        }
                        .frame(maxWidth: .infinity, minHeight: 104, alignment: .bottom)
                        .contentShape(Rectangle())
                    }
                    .buttonStyle(.plain)
                    .accessibilityLabel("\(Self.label(day.date)), \(fmt(day.totals.kcal)) kcal")
                }
            }
            .overlay(alignment: .bottom) {
                // dashed calorie target line
                if target > 0 {
                    GeometryReader { geo in
                        Path { p in
                            let y = geo.size.height - 22 - 84 * target / maxKcal
                            p.move(to: CGPoint(x: 0, y: y)); p.addLine(to: CGPoint(x: geo.size.width, y: y))
                        }
                        .stroke(Theme.secondary.opacity(0.5), style: StrokeStyle(lineWidth: 1, dash: [4, 4]))
                    }
                    .allowsHitTesting(false)
                }
            }
            Text("Tap a day to see its log. Dashed line is your \(fmt(target)) kcal target.")
                .font(Theme.text(11.5)).foregroundStyle(Theme.secondary)
        }
        .card(22)
    }

    // MARK: Logging

    private var describeField: some View {
        HStack(spacing: 4) {
            TextField("Describe what you ate…", text: $text)
                .font(Theme.text(15)).padding(.horizontal, 10).frame(height: 40)
                .submitLabel(.done)
                .onSubmit(logText)
                .disabled(logging)
            if logging {
                ProgressView().frame(width: 40, height: 40)
            } else {
                Button { store.openCoach(prefill: "") } label: {
                    Image(systemName: "camera").frame(width: 40, height: 40).background(Theme.trainTrack, in: Circle())
                }
                .buttonStyle(.plain)
                .accessibilityLabel("Log a meal photo in chat")
            }
        }
        .font(.system(size: 15))
        .foregroundStyle(Theme.ink)
        .padding(6)
        .background(.white, in: RoundedRectangle(cornerRadius: 24, style: .continuous))
        .overlay(RoundedRectangle(cornerRadius: 24, style: .continuous).stroke(Theme.border))
    }

    private func logText() {
        let t = text.trimmingCharacters(in: .whitespaces)
        guard !t.isEmpty else { return }
        text = ""
        logging = true
        Task {
            await store.send("Log for \(Self.mealNow): \(t)") // send() refreshes food + today
            await store.refreshFoodWeek()
            logging = false
        }
    }

    static var mealNow: String {
        switch Calendar.current.component(.hour, from: Date()) {
        case ..<11: "breakfast"
        case ..<16: "lunch"
        case ..<18: "snack"
        default: "dinner"
        }
    }

    @ViewBuilder
    private func mealSection(key: String, title: String, items: [FoodLog]) -> some View {
        Section {
            HStack(alignment: .firstTextBaseline) {
                Text(title).font(Theme.text(15, .semibold))
                Spacer()
                if items.isEmpty {
                    Text("Not logged").font(Theme.text(12.5)).foregroundStyle(Theme.secondary)
                } else {
                    (Text(fmt(items.map(\.kcal).reduce(0, +))).font(Theme.mono(14)) + Text(" kcal").font(Theme.text(12)).foregroundColor(Theme.secondary))
                }
            }
            .listRowSeparator(.hidden)

            ForEach(items) { item in
                FoodRow(item: item)
                    .listRowSeparator(.hidden)
                    .swipeActions {
                        Button("Delete", role: .destructive) {
                            Task { await store.deleteFood(item.id); await store.refreshFoodWeek() }
                        }
                    }
            }
        }
        .listRowBackground(Color.white)
    }

    private var plansHeader: some View {
        HStack {
            Text("DIET PLANS").font(Theme.text(11.5, .semibold)).tracking(0.6).foregroundStyle(Theme.secondary)
            Spacer()
            Button { store.openCoach(prefill: "Create a diet plan for me: ") } label: {
                Label("New plan", systemImage: "plus").font(Theme.text(13, .semibold))
                    .padding(.horizontal, 12).frame(minHeight: 32)
                    .background(Theme.ink, in: Capsule()).foregroundStyle(.white)
            }
            .buttonStyle(.plain)
            .frame(minHeight: 44)
        }
        .padding(.horizontal, 4).padding(.top, 8)
    }
}

// MARK: - Rows and cards

private struct FoodRow: View {
    var item: FoodLog
    var body: some View {
        HStack(spacing: 10) {
            VStack(alignment: .leading, spacing: 1) {
                Text(item.name).font(Theme.text(14, .medium))
                (Text("\(item.quantity.isEmpty ? "" : item.quantity + " · ")\(fmt(item.proteinG)) P · \(fmt(item.carbsG)) C · \(fmt(item.fatG)) F")
                    + (item.source == "coach" ? Text(" · ") + Text("via Milo").foregroundColor(Theme.fuel).fontWeight(.medium) : Text("")))
                    .font(Theme.text(12)).foregroundColor(Theme.secondary)
            }
            Spacer()
            Text(fmt(item.kcal)).font(Theme.mono(13.5, .regular)).foregroundStyle(Theme.tertiary)
        }
        .frame(minHeight: 44)
    }
}

/// A diet plan's meals, each with a Log button (logs to the day shown) or a check once logged that day.
private struct DietPlanCard: View {
    @Environment(AppStore.self) private var store
    var plan: DietPlan
    var title: String?
    var expanded: Bool
    var toggle: (() -> Void)? = nil
    @State private var loggingMeal: String?

    private func isLogged(_ m: DietMeal) -> Bool {
        (store.food?.meals[m.meal] ?? []).contains { $0.name == m.name }
    }

    var body: some View {
        let protein = plan.meals.map(\.proteinG).reduce(0, +)
        VStack(alignment: .leading, spacing: 12) {
            Button { toggle?() } label: {
                HStack(alignment: .top, spacing: 10) {
                    Image(systemName: "fork.knife").font(.system(size: 13, weight: .semibold)).foregroundStyle(Theme.fuel)
                        .frame(width: 30, height: 30).background(Theme.fuelTint2, in: Circle())
                    VStack(alignment: .leading, spacing: 1) {
                        if let title { Text(title.uppercased()).font(Theme.text(10.5, .semibold)).tracking(0.5).foregroundStyle(Theme.fuelDeep) }
                        Text(plan.name).font(Theme.text(15, .semibold))
                        Text("\(fmt(plan.kcal)) kcal · \(fmt(protein)) g protein · \(plan.meals.count) meals")
                            .font(Theme.text(12)).foregroundStyle(Theme.secondary)
                    }
                    Spacer()
                    if toggle != nil {
                        Image(systemName: expanded ? "chevron.up" : "chevron.down").font(.system(size: 12, weight: .semibold))
                            .foregroundStyle(Theme.secondary).padding(.top, 8)
                    }
                }
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .allowsHitTesting(toggle != nil) // not .disabled: that would dim the header

            if expanded {
                VStack(spacing: 0) {
                    ForEach(Array(plan.meals.enumerated()), id: \.offset) { i, m in
                        if i > 0 { Divider().overlay(Theme.hairline) }
                        HStack(spacing: 10) {
                            VStack(alignment: .leading, spacing: 1) {
                                Text(m.meal.capitalized).font(Theme.text(11, .semibold)).foregroundStyle(Theme.secondary)
                                Text(m.name).font(Theme.text(13.5, .medium)).fixedSize(horizontal: false, vertical: true)
                                Text("\(fmt(m.kcal)) kcal · \(fmt(m.proteinG)) P · \(fmt(m.carbsG)) C · \(fmt(m.fatG)) F")
                                    .font(Theme.text(11.5)).foregroundStyle(Theme.secondary)
                            }
                            Spacer(minLength: 8)
                            if isLogged(m) {
                                Label("Logged", systemImage: "checkmark").font(Theme.text(12, .semibold))
                                    .foregroundStyle(Theme.fuel).labelStyle(.titleAndIcon)
                            } else {
                                Button { log(m) } label: {
                                    Group {
                                        if loggingMeal == m.name { ProgressView() } else { Text("Log") }
                                    }
                                    .font(Theme.text(13, .semibold)).frame(width: 56, height: 32)
                                    .background(Theme.fuel, in: Capsule()).foregroundStyle(.white)
                                }
                                .buttonStyle(.plain)
                                .frame(minHeight: 44)
                                .disabled(loggingMeal != nil)
                            }
                        }
                        .padding(.vertical, 8)
                    }
                }
            }
        }
        .card(20, padding: 16)
    }

    private func log(_ m: DietMeal) {
        loggingMeal = m.name
        Task {
            await store.logFood(NewFood(date: store.foodDate, meal: m.meal, name: m.name, quantity: "1 serving",
                                        kcal: m.kcal, proteinG: m.proteinG, carbsG: m.carbsG, fatG: m.fatG, source: "manual"))
            loggingMeal = nil
        }
    }
}
