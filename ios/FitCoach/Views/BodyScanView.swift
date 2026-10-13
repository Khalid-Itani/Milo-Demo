import Charts
import SwiftUI
import UIKit
import VisualizeSDK

// Visualize AI body scan card on Today. The SDK needs a TrueDepth (Face ID) iPhone; the
// publishable key comes from Info.plist (VZPublishableKey). Results upload to POST /scans.

private struct MetricInfo { let id: String; let label: String; let unit: String }

private let composition = [
    MetricInfo(id: "lean_muscle_mass", label: "Lean mass", unit: "kg"),
    MetricInfo(id: "bone_mineral_content", label: "Bone mineral", unit: "kg"),
]
private let girths = [
    MetricInfo(id: "neck_circumference", label: "Neck", unit: "cm"),
    MetricInfo(id: "waist_circumference", label: "Waist", unit: "cm"),
    MetricInfo(id: "lower_waist_circumference", label: "Lower waist", unit: "cm"),
    MetricInfo(id: "hip_circumference", label: "Hip", unit: "cm"),
]
private let indicators = [
    MetricInfo(id: "waist_hip_ratio", label: "Waist-to-hip ratio", unit: ""),
    MetricInfo(id: "waist_height_ratio", label: "Waist-to-height ratio", unit: ""),
    MetricInfo(id: "central_adiposity_index", label: "Central adiposity index", unit: ""),
    MetricInfo(id: "fat_mass_index", label: "Fat mass index", unit: ""),
    MetricInfo(id: "skeletal_muscle_index", label: "Skeletal muscle index", unit: ""),
    MetricInfo(id: "muscle_preservation_index", label: "Muscle preservation", unit: ""),
]

struct BodyScanButton: View {
    @Environment(AppStore.self) private var store
    @State private var supported = false
    @State private var setupError: String?
    @State private var scanning = false
    @State private var askConsent = false
    @State private var showHistory = false

    private var scan: Scan? { store.today?.latestScan }
    private func value(_ id: String) -> Double? { scan?.metrics.first { $0.identifier == id }?.value }

    var body: some View {
        VStack(alignment: .leading, spacing: 18) {
            header
            hero
            section("Measurements") {
                LazyVGrid(columns: [GridItem(.flexible(), spacing: 8), GridItem(.flexible())], spacing: 8) {
                    ForEach(girths, id: \.id) { m in
                        VStack(alignment: .leading, spacing: 4) {
                            Text(m.label).font(Theme.text(12)).foregroundStyle(Theme.secondary)
                            number(value(m.id), unit: m.unit, size: 17)
                        }
                        .padding(12)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .background(Theme.chip, in: RoundedRectangle(cornerRadius: 14, style: .continuous))
                    }
                }
            }
            section("Health indicators") {
                VStack(spacing: 0) {
                    ForEach(Array(indicators.enumerated()), id: \.element.id) { i, m in
                        HStack {
                            Text(m.label).font(Theme.text(14)).foregroundStyle(Theme.ink)
                            Spacer()
                            Text(value(m.id).map { String(format: "%.2f", $0) } ?? "—")
                                .font(Theme.mono(14)).foregroundStyle(value(m.id) == nil ? Theme.secondary : Theme.ink)
                        }
                        .frame(minHeight: 36)
                        if i < indicators.count - 1 { Divider().overlay(Theme.hairline) }
                    }
                }
            }
            Toggle(isOn: Binding(get: { store.profile?.scanAiSharingConsent == true },
                                 set: { on in Task { await store.setScanAISharing(on) } })) {
                VStack(alignment: .leading, spacing: 2) {
                    Text("Let Milo use my scans").font(Theme.text(14.5, .semibold))
                    Text("Milo sees your latest scan results when planning diet and training.")
                        .font(Theme.text(12)).foregroundStyle(Theme.secondary)
                }
            }
            .tint(Theme.train)
            Button { showHistory = true } label: {
                HStack {
                    Image(systemName: "chart.line.uptrend.xyaxis")
                    Text("Scan history").font(Theme.text(14.5, .semibold))
                    Spacer()
                    Image(systemName: "chevron.right").font(.system(size: 13, weight: .semibold)).foregroundStyle(Theme.secondary)
                }
                .padding(.horizontal, 14)
                .frame(minHeight: 48)
                .background(Theme.chip, in: RoundedRectangle(cornerRadius: 14, style: .continuous))
                .foregroundStyle(Theme.ink)
            }
            .buttonStyle(.plain)
        }
        .foregroundStyle(Theme.ink)
        .card(22, padding: 18)
        .task {
            do { try await Visualize.configureFromHostConfiguration(engine: AVIXScanEngine()) }
            catch { setupError = String(describing: error) }
            supported = await Visualize.isDeviceSupported
        }
        .sheet(isPresented: $showHistory) { ScanHistoryView() }
        .alert("Save scan results?", isPresented: $askConsent) {
            Button("Allow") { Task { await store.allowScanStorage(); await startScan() } }
            Button("Not now", role: .cancel) {}
        } message: {
            Text("Milo stores a summary of each body scan (body fat, lean mass, measurements) so it can show on Today. Only the numbers are saved, never images.")
        }
    }

    // MARK: Pieces

    private var header: some View {
        HStack(spacing: 10) {
            Image(systemName: "figure.arms.open")
                .font(.system(size: 15, weight: .semibold))
                .frame(width: 32, height: 32)
                .background(Theme.trainTint, in: Circle())
                .foregroundStyle(Theme.train)
            VStack(alignment: .leading, spacing: 1) {
                Text("Body scan").font(Theme.text(15, .semibold))
                Text(scan.map { "Latest · \(Self.day($0.capturedAt))" } ?? "Powered by Visualize")
                    .font(Theme.text(12)).foregroundStyle(Theme.secondary)
            }
            Spacer()
            Button(action: tapScan) {
                Text(scanning ? "Scanning…" : scan == nil ? "Start scan" : "Rescan")
                    .font(Theme.text(13.5, .semibold))
                    .padding(.horizontal, 14).frame(minHeight: 36)
                    .background(Theme.ink, in: Capsule())
                    .foregroundStyle(.white)
            }
            .buttonStyle(.plain)
            .disabled(scanning)
            .frame(minHeight: 44)
        }
    }

    private var hero: some View {
        HStack(alignment: .top, spacing: 16) {
            VStack(alignment: .leading, spacing: 6) {
                (Text(value("body_fat_percentage").map { String(format: "%.1f", $0) } ?? "—").font(Theme.mono(34))
                 + Text(" %").font(Theme.text(15)).foregroundColor(Theme.secondary))
                Text("Body fat").font(Theme.text(12.5)).foregroundStyle(Theme.secondary)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            VStack(alignment: .leading, spacing: 12) {
                ForEach(composition, id: \.id) { m in
                    VStack(alignment: .leading, spacing: 2) {
                        Text(m.label).font(Theme.text(12)).foregroundStyle(Theme.secondary)
                        number(value(m.id), unit: m.unit, size: 17)
                    }
                }
            }
            .frame(width: 112, alignment: .leading)
        }
    }

    private func section<Content: View>(_ title: String, @ViewBuilder _ content: () -> Content) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            Text(title.uppercased()).font(Theme.text(11, .semibold)).tracking(0.6).foregroundStyle(Theme.secondary)
            content()
        }
    }

    private func number(_ v: Double?, unit: String, size: CGFloat) -> some View {
        (Text(v.map(fmt) ?? "—").font(Theme.mono(size)) + Text(" \(unit)").font(Theme.text(12)).foregroundColor(Theme.secondary))
            .foregroundStyle(Theme.ink)
    }

    static func date(_ iso: String) -> Date? {
        let f = ISO8601DateFormatter()
        if let d = f.date(from: iso) { return d }
        f.formatOptions.insert(.withFractionalSeconds)
        return f.date(from: iso)
    }

    private static func day(_ iso: String) -> String {
        let f = ISO8601DateFormatter()
        guard let d = f.date(from: iso) ?? { f.formatOptions.insert(.withFractionalSeconds); return f.date(from: iso) }() else { return "" }
        return d.formatted(.dateTime.month(.abbreviated).day())
    }

    // MARK: Scan flow

    private func tapScan() {
        if let setupError { store.error = "Visualize setup failed: \(setupError)" }
        else if !supported { store.error = "Body scans need an iPhone with Face ID (TrueDepth camera)." }
        else if store.profile?.scanStorageConsent == true { Task { await startScan() } }
        else { askConsent = true }
    }

    private func startScan() async {
        guard let p = store.profile, let cm = p.heightCm, let kg = p.weightKg, let age = p.age,
              let vc = UIApplication.shared.connectedScenes
                .compactMap({ ($0 as? UIWindowScene)?.keyWindow?.rootViewController }).first
        else { store.error = "Add height, weight and age first."; return }
        var top = vc
        while let presented = top.presentedViewController { top = presented }

        scanning = true
        defer { scanning = false }
        do {
            let token = try await store.visualizeSessionToken()
            let subject = ScanSubject(gender: p.sex == "female" ? .female : .male,
                                      heightCm: Float(cm), weightKg: Float(kg), ageYears: age)
            let result = try await Visualize.startScan(sessionToken: token, subject: subject, presentingFrom: top)
            await store.saveBodyScan(metrics: Self.metrics(from: result.measurements))
        } catch {
            store.error = error.localizedDescription
        }
    }

    /// SDK units (lb, in) -> backend units (kg, cm); missing or non-positive values are skipped.
    static func metrics(from m: Measurements) -> [ScanMetric] {
        var out: [ScanMetric] = []
        func add(_ id: String, _ v: Float?, _ unit: String, _ factor: Double = 1) {
            guard let v, v > 0, v.isFinite else { return }
            out.append(ScanMetric(identifier: id, value: (Double(v) * factor * 100).rounded() / 100, unit: unit))
        }
        let lbToKg = 0.45359237, inToCm = 2.54
        add("body_fat_percentage", m.bodyFatPercent, "percent")
        add("lean_muscle_mass", m.leanMuscleMassLb, "kg", lbToKg)
        add("bone_mineral_content", m.boneMineralContentLb, "kg", lbToKg)
        add("neck_circumference", m.girths.neckIn, "cm", inToCm)
        add("waist_circumference", m.girths.waistIn, "cm", inToCm)
        add("lower_waist_circumference", m.girths.lowerWaistIn, "cm", inToCm)
        add("hip_circumference", m.girths.hipIn, "cm", inToCm)
        if let a = m.advanced {
            add("waist_hip_ratio", a.waistHipRatio, "ratio")
            add("waist_height_ratio", a.waistHeightRatio, "ratio")
            add("central_adiposity_index", a.centralAdiposityIndex, "index")
            add("fat_mass_index", a.fatMassIndex, "index")
            add("skeletal_muscle_index", a.skeletalMuscleIndex, "index")
            add("muscle_preservation_index", a.musclePreservationIndex, "index")
        }
        return out
    }
}

// MARK: - History

private let allMetrics = [MetricInfo(id: "body_fat_percentage", label: "Body fat", unit: "%")] + composition + girths
    + indicators

struct ScanHistoryView: View {
    @Environment(AppStore.self) private var store
    @Environment(\.dismiss) private var dismiss

    private var fatPoints: [(Date, Double)] {
        store.scans.compactMap { scan in
            guard let d = BodyScanButton.date(scan.capturedAt),
                  let v = scan.metrics.first(where: { $0.identifier == "body_fat_percentage" })?.value else { return nil }
            return (d, v)
        }
    }

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(spacing: 14) {
                    if fatPoints.count >= 2 { trend }
                    if store.scans.isEmpty {
                        Text("No scans yet. Run a body scan from Today and it will show up here.")
                            .font(Theme.text(14)).foregroundStyle(Theme.secondary)
                            .frame(maxWidth: .infinity).padding(.top, 40)
                    }
                    ForEach(store.scans, id: \.id) { scanCard($0) }
                }
                .padding(16)
            }
            .background(Theme.bg)
            .navigationTitle("Scan history")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar { ToolbarItem(placement: .confirmationAction) { Button("Done") { dismiss() } } }
            .refreshable { await store.refreshScans() }
            .task { await store.refreshScans() }
        }
    }

    private var trend: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("BODY FAT TREND").font(Theme.text(11, .semibold)).tracking(0.6).foregroundStyle(Theme.secondary)
            Chart(fatPoints, id: \.0) { point in
                LineMark(x: .value("Date", point.0), y: .value("Body fat", point.1))
                    .foregroundStyle(Theme.train).interpolationMethod(.monotone)
                PointMark(x: .value("Date", point.0), y: .value("Body fat", point.1))
                    .foregroundStyle(Theme.train)
            }
            .chartYScale(domain: .automatic(includesZero: false))
            .frame(height: 160)
        }
        .card(22, padding: 18)
    }

    private func scanCard(_ scan: Scan) -> some View {
        let values = allMetrics.compactMap { m in scan.metrics.first { $0.identifier == m.id }.map { (m, $0.value) } }
        return VStack(alignment: .leading, spacing: 12) {
            Text(BodyScanButton.date(scan.capturedAt)?.formatted(.dateTime.month(.abbreviated).day().hour().minute()) ?? scan.capturedAt)
                .font(Theme.text(15, .semibold))
            LazyVGrid(columns: [GridItem(.flexible(), spacing: 8), GridItem(.flexible())], alignment: .leading, spacing: 10) {
                ForEach(values, id: \.0.id) { m, v in
                    VStack(alignment: .leading, spacing: 2) {
                        Text(m.label).font(Theme.text(12)).foregroundStyle(Theme.secondary).lineLimit(1)
                        (Text(m.unit.isEmpty ? String(format: "%.2f", v) : fmt(v)).font(Theme.mono(15))
                         + Text(m.unit.isEmpty ? "" : " \(m.unit)").font(Theme.text(11.5)).foregroundColor(Theme.secondary))
                    }
                }
            }
        }
        .foregroundStyle(Theme.ink)
        .card(22, padding: 18)
    }
}
