import SwiftUI

struct OverlayView: View {
    @ObservedObject var viewModel: OverlayViewModel
    @FocusState private var composerFocused: Bool
    @State private var pointerCueVisible = false
    @State private var showsRouteDetails = false
    @State private var showsPulse = false
    @ObservedObject private var proactive = ProactiveController.shared

    private var isExpanded: Bool {
        viewModel.presentationState == .expanded
    }

    private var outerCornerRadius: CGFloat {
        36
    }

    private var innerFieldCornerRadius: CGFloat {
        24
    }

    private var showsPointerAnchorCue: Bool {
        viewModel.summonStyle == .pointerPinned && !isExpanded && !usesFloatingPointerPrompt
    }

    private var usesFloatingPointerPrompt: Bool {
        viewModel.summonStyle == .pointerPinned && !isExpanded
    }

    private var usesCenteredCompactPrompt: Bool {
        viewModel.summonStyle == .centered && !isExpanded
    }

    private var shellTopInset: CGFloat {
        guard showsPointerAnchorCue else { return 0 }
        return viewModel.anchorEdge == .top ? 18 : 0
    }

    private var shellBottomInset: CGFloat {
        guard showsPointerAnchorCue else { return 0 }
        return viewModel.anchorEdge == .bottom ? 18 : 0
    }

    var body: some View {
        ZStack {
            if showsPointerAnchorCue {
                pointerAnchorCue
            }

            if usesFloatingPointerPrompt {
                floatingPointerPrompt
            } else {
                GlassBackgroundView()
                    .clipShape(RoundedRectangle(cornerRadius: outerCornerRadius, style: .continuous))
                    .overlay(
                        RoundedRectangle(cornerRadius: outerCornerRadius, style: .continuous)
                            .fill(
                                LinearGradient(
                                    colors: [
                                        Color.black.opacity(0.24),
                                        Color.black.opacity(0.36),
                                    ],
                                    startPoint: .topLeading,
                                    endPoint: .bottomTrailing
                                )
                            )
                    )
                    .overlay(
                        RoundedRectangle(cornerRadius: outerCornerRadius, style: .continuous)
                            .stroke(Color.white.opacity(0.18), lineWidth: 1)
                    )
                    .padding(.top, shellTopInset)
                    .padding(.bottom, shellBottomInset)

                VStack(alignment: .leading, spacing: 14) {
                    composerBar(
                        textSize: 21,
                        iconSize: 15,
                        horizontalPadding: 20,
                        verticalPadding: 14,
                        showReturnHint: !isExpanded
                    )

                    if usesCenteredCompactPrompt, let detail = centeredCompactDetailText {
                        centeredCompactDetailRow(detail)
                    } else if !isExpanded, let selectedText = viewModel.visualContext?.selected_text,
                              !selectedText.isEmpty {
                        selectedTextChip(selectedText)
                    } else if !isExpanded, let footer = viewModel.footer, !footer.isEmpty {
                        compactStatusChip(footer)
                    }

                    if isExpanded {
                        expandedContent
                    } else {
                        Spacer(minLength: 0)
                    }
                }
                .padding(isExpanded ? 20 : 16)
                .padding(.top, shellTopInset)
                .padding(.bottom, shellBottomInset)
                .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .top)
            }
        }
        .padding(8)
        .frame(maxWidth: .infinity)
        .frame(height: viewModel.preferredHeight)
        .compositingGroup()
        .environment(\.openURL, OpenURLAction { url in
            if url.isFileURL {
                NSWorkspace.shared.activateFileViewerSelecting([url])
                return .handled
            }
            return ["https", "http"].contains(url.scheme ?? "") ? .systemAction : .discarded
        })
        .onAppear {
            composerFocused = true
            updatePointerCueVisibility(animated: false)
        }
        .onChange(of: viewModel.composerFocusRequest) {
            composerFocused = false
            DispatchQueue.main.async {
                composerFocused = true
            }
        }
        .onChange(of: showsPointerAnchorCue) {
            updatePointerCueVisibility(animated: true)
        }
        .onChange(of: viewModel.anchorEdge) {
            updatePointerCueVisibility(animated: true)
        }
        .onChange(of: viewModel.anchorXFraction) {
            if showsPointerAnchorCue {
                pointerCueVisible = true
            }
        }
        .animation(.spring(response: 0.28, dampingFraction: 0.9), value: viewModel.presentationState)
    }

    private var expandedContent: some View {
        HStack(alignment: .top, spacing: 18) {
            VStack(alignment: .leading, spacing: 12) {
                HStack(spacing: 8) {
                    Label(routeBadgeLabel, systemImage: routeBadgeIconName)
                        .font(.system(size: 11, weight: .medium))
                        .foregroundStyle(.white.opacity(0.7))
                    if let selection = viewModel.visualContext?.selected_text, !selection.isEmpty {
                        Text(selection)
                            .font(.system(size: 12, weight: .medium))
                            .foregroundStyle(.white.opacity(0.9))
                            .lineLimit(1)
                            .truncationMode(.middle)
                    }
                    Spacer(minLength: 8)
                    if viewModel.visualContext != nil {
                        Button(action: viewModel.detachVisualContext) {
                            Image(systemName: "xmark.circle")
                        }
                        .buttonStyle(.plain)
                        .foregroundStyle(.white.opacity(0.55))
                        .disabled(viewModel.isLoading)
                        .help("Detach screen or selection context; keep this conversation")
                        .accessibilityLabel("Detach context")
                    }
                    Button { showsRouteDetails.toggle() } label: {
                        Image(systemName: "info.circle")
                    }
                    .buttonStyle(.plain)
                    .foregroundStyle(.white.opacity(0.55))
                    .help("Routing and runtime details")
                    .popover(isPresented: $showsRouteDetails) {
                        VStack(alignment: .leading, spacing: 12) {
                            routeInspector(reason: viewModel.routeReasonText ?? "Waiting for a request.", candidates: viewModel.routeCandidateSummary)
                            if let footer = viewModel.runtimeDetails ?? viewModel.footer {
                                Text(footer).font(.caption).textSelection(.enabled)
                            }
                        }
                        .padding(16)
                        .frame(width: 340)
                        .preferredColorScheme(.dark)
                    }
                }
                .padding(.horizontal, 8)

                ScrollViewReader { scroll in
                  ScrollView {
                    VStack(alignment: .leading, spacing: 16) {
                            ForEach(viewModel.conversationTurns) { turn in
                                conversationPrompt(turn.prompt)
                                Text(replyText(turn.reply))
                                    .font(.system(size: 16))
                                    .foregroundStyle(.white.opacity(0.85))
                                    .textSelection(.enabled)
                                    .frame(maxWidth: .infinity, alignment: .leading)
                                Divider().overlay(.white.opacity(0.08))
                            }
                            if !viewModel.submittedPrompt.isEmpty {
                                conversationPrompt(viewModel.submittedPrompt)
                            }
                            if let importStatus = viewModel.importStatus, !importStatus.isEmpty {
                                Text(importStatus).font(.caption).foregroundStyle(.white.opacity(0.65))
                            }
                            if !viewModel.commandTasks.isEmpty {
                                commandTaskStrip
                                commandOutcomeStrip
                            }

                            if viewModel.response.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty && viewModel.isLoading {
                                thinkingState
                            } else {
                                Text(replyText(viewModel.response))
                                    .font(.system(size: 16, weight: .regular))
                                    .foregroundStyle(Color.white.opacity(0.95))
                                    .frame(maxWidth: .infinity, alignment: .leading)
                                    .lineSpacing(5)
                                    .textSelection(.enabled)
                            }
                            Color.clear.frame(height: 1).id("conversation-bottom")
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(18)
                  }
        .onAppear {
                      scroll.scrollTo("conversation-bottom", anchor: .bottom)
                  }
                  .onChange(of: viewModel.submittedPrompt) {
                      scroll.scrollTo("conversation-bottom", anchor: .bottom)
                  }
                  .onChange(of: viewModel.response) {
                      if viewModel.isLoading {
                          scroll.scrollTo("conversation-bottom", anchor: .bottom)
                      }
                  }
                }
                .frame(maxWidth: .infinity, maxHeight: .infinity)
                .background(Color.black.opacity(0.12), in: RoundedRectangle(cornerRadius: 20))
                .layoutPriority(1)

                HStack(spacing: 16) {
                    if viewModel.isLoading, let activeStep = viewModel.activeProgressStep {
                        ProgressView().controlSize(.small)
                        Text(activeStep)
                            .font(.system(size: 11, weight: .medium))
                            .lineLimit(1)
                    } else {
                        Text("Cleo").font(.system(size: 11, weight: .semibold))
                    }
                    Spacer(minLength: 8)
                    Button { showsPulse.toggle() } label: {
                        HStack(spacing: 5) {
                            Image(systemName: "waveform.path")
                            Text(proactive.snapshot?.suggestions.isEmpty == false ? "Suggestion" : "Pulse")
                        }
                        .foregroundStyle(proactive.snapshot?.enabled == true ? Color.mint : Color.secondary)
                    }
                    .help("Goals, research mode and proactive activity")
                    .popover(isPresented: $showsPulse) {
                        ProactivePanel(controller: proactive) { prompt in
                            showsPulse = false
                            viewModel.detachVisualContext()
                            viewModel.query = prompt
                            viewModel.focusComposer()
                        }
                    }
                    Button(action: viewModel.copyResponse) {
                        Image(systemName: "doc.on.doc")
                    }
                    .disabled(viewModel.response.isEmpty)
                    .help("Copy latest response")
                    .accessibilityLabel("Copy latest response")
                    Menu {
                        Button("Edit Last Request", action: viewModel.editLastRequest)
                            .disabled(viewModel.isLoading || viewModel.submittedPrompt.isEmpty)
                        Divider()
                        Button("Memory", action: viewModel.showMemoryPanel)
                        Button("Graph", action: viewModel.showGraphPanel)
                        Button("Import ChatGPT Export", action: viewModel.importChatGPTExport)
                        Divider()
                        Picker("Response Quality", selection: $viewModel.responseMode) {
                            ForEach(OverlayResponseMode.allCases) { mode in
                                Text(mode.title).tag(mode)
                            }
                        }
                        if viewModel.shouldShowSpeechSetupCard {
                            Divider()
                            Button("Finish Voice Setup", action: viewModel.openSpeechSettings)
                        }
                    } label: {
                        Image(systemName: "ellipsis.circle")
                    }
                    .menuStyle(.borderlessButton)
                    .fixedSize()
                    .help("Memory, imports and response quality")
                    Button("Clear", action: viewModel.clear)
                    Button { viewModel.collapse() } label: {
                        Image(systemName: "chevron.up")
                    }
                    .help("Collapse")
                }
                .font(.system(size: 12, weight: .medium))
                .buttonStyle(.plain)
                .foregroundStyle(.white.opacity(0.65))
                .padding(.horizontal, 8)
                .padding(.vertical, 4)
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity)

            if viewModel.isShowingMemoryPanel {
                HStack(spacing: 0) {
                    resizeHandle
                    WorkspacePanelView(viewModel: viewModel)
                        .frame(width: viewModel.workspacePanelWidth)
                }
                .transition(.move(edge: .trailing).combined(with: .opacity))
            }
        }
        .transition(.move(edge: .top).combined(with: .opacity))
    }

    private func replyText(_ text: String) -> AttributedString {
        (try? AttributedString(markdown: text, options: .init(interpretedSyntax: .inlineOnlyPreservingWhitespace))) ?? AttributedString(text)
    }

    private func conversationPrompt(_ text: String) -> some View {
        Text(text)
            .font(.system(size: 14, weight: .medium))
            .foregroundStyle(.white.opacity(0.92))
            .textSelection(.enabled)
            .padding(.horizontal, 14)
            .padding(.vertical, 10)
            .background(.white.opacity(0.08), in: RoundedRectangle(cornerRadius: 14))
            .frame(maxWidth: .infinity, alignment: .trailing)
    }

    private var subtitleText: String {
        if viewModel.lastInteractionMode == "command" {
            return "Command workflow with specialist actions and shared memory"
        }
        return "One assistant, shared memory, local-first workflows"
    }

    private var floatingPointerPrompt: some View {
        floatingPromptShell
    }

    private var floatingPromptShell: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(alignment: .center, spacing: 8) {
                routeBadge

                TextField("Ask Cleo about this...", text: $viewModel.query)
                    .textFieldStyle(.plain)
                    .font(.system(size: 15, weight: .semibold, design: .rounded))
                    .foregroundStyle(Color.white.opacity(0.94))
                    .focused($composerFocused)
                    .submitLabel(.go)
                    .onSubmit {
                        viewModel.submit()
                    }

                pointerTrailingAction
            }

            if let selectedText = viewModel.visualContext?.selected_text,
               !selectedText.isEmpty {
                Text(selectedText)
                    .font(.system(size: 10, weight: .semibold, design: .rounded))
                    .foregroundStyle(Color.white.opacity(0.78))
                    .lineLimit(1)
            } else if let footer = viewModel.footer, !footer.isEmpty {
                Text(footer)
                    .font(.system(size: 10, weight: .medium, design: .rounded))
                    .foregroundStyle(Color.white.opacity(0.6))
                    .lineLimit(1)
            } else {
                Text(pointerPromptSupportingText)
                    .font(.system(size: 10, weight: .medium, design: .rounded))
                    .foregroundStyle(Color.white.opacity(0.48))
                    .lineLimit(1)
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .center)
        .padding(.horizontal, 13)
        .padding(.top, viewModel.anchorEdge == .top ? 25 : 11)
        .padding(.bottom, viewModel.anchorEdge == .bottom ? 24 : 10)
        .background(
            PointerPromptBubble(edge: viewModel.anchorEdge, tailFraction: viewModel.anchorXFraction)
                .fill(
                    LinearGradient(
                        colors: [
                            Color(red: 0.17, green: 0.18, blue: 0.2).opacity(0.96),
                            Color(red: 0.17, green: 0.18, blue: 0.2).opacity(0.94),
                            Color(red: 0.13, green: 0.14, blue: 0.16).opacity(0.96),
                        ],
                        startPoint: .topLeading,
                        endPoint: .bottomTrailing
                    )
                )
        )
        .overlay(
            PointerPromptBubble(edge: viewModel.anchorEdge, tailFraction: viewModel.anchorXFraction)
                .stroke(Color.white.opacity(0.12), lineWidth: 1)
        )
        .background(
            PointerPromptBubble(edge: viewModel.anchorEdge, tailFraction: viewModel.anchorXFraction)
                .fill(Color.black.opacity(0.12))
                .blur(radius: 10)
        )
    }

    private var routeBadge: some View {
        HStack(spacing: 6) {
            Image(systemName: routeBadgeIconName)
                .font(.system(size: 10, weight: .semibold))
            Text(routeBadgeLabel)
                .font(.system(size: 9, weight: .bold, design: .rounded))
        }
        .foregroundStyle(Color.white.opacity(0.78))
        .padding(.horizontal, 8)
        .padding(.vertical, 5)
        .background(
            Capsule(style: .continuous)
                .fill(Color.white.opacity(0.07))
        )
        .overlay(
            Capsule(style: .continuous)
                .stroke(Color.white.opacity(0.08), lineWidth: 1)
        )
    }

    private var pointerTrailingAction: some View {
        Group {
            if viewModel.isLoading {
                Button(action: {
                    viewModel.stopCurrentRequest()
                }) {
                    Image(systemName: "stop.fill")
                        .font(.system(size: 11, weight: .black))
                        .foregroundStyle(Color.white.opacity(0.92))
                        .frame(width: 24, height: 24)
                        .background(
                            Circle()
                                .fill(Color.white.opacity(0.09))
                        )
                }
                .buttonStyle(.plain)
            } else {
                Button(action: {
                    viewModel.submit()
                }) {
                    Image(systemName: "arrow.right")
                        .font(.system(size: 12, weight: .bold))
                        .foregroundStyle(Color.white.opacity(0.92))
                        .frame(width: 24, height: 24)
                        .background(
                            Circle()
                                .fill(Color.white.opacity(0.06))
                        )
                        .overlay(
                            Circle()
                                .stroke(Color.white.opacity(0.08), lineWidth: 1)
                        )
                }
                .buttonStyle(.plain)
            }
        }
    }

    private var pointerPromptSupportingText: String {
        if let routeReasonText = viewModel.routeReasonText {
            return routeReasonText
        }

        switch routeBadgeLabel {
        case "Selection":
            return "Focused on what you selected"
        case "Glance":
            return "Focused on the nearby pointer region"
        case "Full":
            return "Looking at the full app window"
        case "Command":
            return "Routing this into actions and specialists"
        case "Visual":
            return "Using the visual stack for what you are seeing"
        case "Chat":
            return "Quick text conversation"
        default:
            return "Quick prompt at your pointer"
        }
    }

    private var centeredCompactDetailText: String? {
        if let selectedText = viewModel.visualContext?.selected_text,
           !selectedText.isEmpty {
            return "Selected: \(selectedText)"
        }
        if let footer = viewModel.footer,
           !footer.isEmpty {
            return footer
        }
        return nil
    }

    private func composerBar(
        textSize: CGFloat,
        iconSize: CGFloat,
        horizontalPadding: CGFloat,
        verticalPadding: CGFloat,
        showReturnHint: Bool
    ) -> some View {
        HStack(spacing: 12) {
            ZStack {
                RoundedRectangle(cornerRadius: 10, style: .continuous)
                    .fill(Color.white.opacity(0.08))
                RoundedRectangle(cornerRadius: 10, style: .continuous)
                    .stroke(Color.white.opacity(0.1), lineWidth: 1)

                if let brandMark = CleoBranding.swiftUIMarkImage() {
                    brandMark
                        .resizable()
                        .renderingMode(.template)
                        .interpolation(.high)
                        .frame(width: iconSize + 2, height: iconSize + 2)
                        .foregroundStyle(Color.white.opacity(0.82))
                } else {
                    Image(systemName: "sparkles")
                        .font(.system(size: iconSize, weight: .semibold))
                        .foregroundStyle(Color.white.opacity(0.56))
                }
            }
            .frame(width: 30, height: 30)

                TextField("Ask or command Cleo...", text: $viewModel.query)
                .textFieldStyle(.plain)
                .font(.system(size: textSize, weight: .medium, design: .rounded))
                .foregroundStyle(.white)
                .frame(minWidth: 0, maxWidth: .infinity, alignment: .leading)
                .layoutPriority(0)
                .focused($composerFocused)
                .submitLabel(.go)
                .onTapGesture {
                    viewModel.expand()
                }
                .onSubmit {
                    viewModel.submit()
                }

            if viewModel.isLoading {
                Button(action: {
                    viewModel.stopCurrentRequest()
                }) {
                    HStack(spacing: 6) {
                        Image(systemName: "stop.fill")
                            .font(.system(size: 10, weight: .bold))
                        Text("Stop")
                            .font(.system(size: 11, weight: .semibold, design: .rounded))
                    }
                    .foregroundStyle(Color.white.opacity(0.88))
                    .padding(.horizontal, 10)
                    .padding(.vertical, 8)
                    .background(
                        Capsule(style: .continuous)
                            .fill(Color.white.opacity(0.11))
                    )
                    .overlay(
                        Capsule(style: .continuous)
                            .stroke(Color.white.opacity(0.12), lineWidth: 1)
                    )
                }
                .buttonStyle(.plain)
            } else {
                if !isExpanded {
                    Button { showsPulse.toggle() } label: {
                        Image(systemName: proactive.snapshot?.suggestions.isEmpty == false ? "bell.badge" : "waveform.path")
                            .foregroundStyle(proactive.snapshot?.enabled == true ? Color.mint : Color.secondary)
                    }
                    .buttonStyle(.plain)
                    .accessibilityLabel("Cleo Pulse")
                    .help("Goals and proactive routines")
                    .popover(isPresented: $showsPulse) {
                        ProactivePanel(controller: proactive) { prompt in
                            showsPulse = false
                            viewModel.detachVisualContext()
                            viewModel.query = prompt
                            viewModel.expand()
                            viewModel.focusComposer()
                        }
                    }
                }
                if !isExpanded && viewModel.hasConversation {
                    Button(action: viewModel.expand) {
                        Image(systemName: "bubble.left.and.bubble.right")
                            .foregroundStyle(.white.opacity(0.7))
                    }
                    .buttonStyle(.plain)
                    .help("Show conversation history")
                    .accessibilityLabel("Show conversation history")
                }
                if !isExpanded {
                    iconStatusChip(systemName: routeBadgeIconName, title: routeBadgeLabel)
                        .fixedSize()
                        .layoutPriority(1)
                }

                Button(action: {
                    viewModel.toggleVoiceInput()
                }) {
                    Image(systemName: viewModel.isListening ? "waveform.circle.fill" : "mic.fill")
                        .font(.system(size: 14, weight: .semibold))
                        .foregroundStyle(viewModel.isListening ? Color.white.opacity(0.95) : Color.white.opacity(0.48))
                        .frame(width: 28, height: 28)
                        .background(
                            RoundedRectangle(cornerRadius: 12, style: .continuous)
                                .fill(viewModel.isListening ? Color.white.opacity(0.16) : Color.clear)
                        )
                }
                .buttonStyle(.plain)

                if showReturnHint && !viewModel.isListening {
                    iconStatusChip(systemName: "arrow.turn.down.left", title: nil)
                }
            }
        }
        .padding(.horizontal, horizontalPadding)
        .padding(.vertical, verticalPadding)
        .background(
            RoundedRectangle(cornerRadius: innerFieldCornerRadius, style: .continuous)
                .fill(Color.white.opacity(0.07))
        )
        .overlay(
            RoundedRectangle(cornerRadius: innerFieldCornerRadius, style: .continuous)
                .stroke(Color.white.opacity(0.08), lineWidth: 1)
        )
    }

    private var pointerAnchorCue: some View {
        GeometryReader { geometry in
            let width = geometry.size.width
            let anchorX = width * viewModel.anchorXFraction
            let glowY: CGFloat = viewModel.anchorEdge == .top ? 10 : geometry.size.height - 10
            let tailY: CGFloat = viewModel.anchorEdge == .top ? 18 : geometry.size.height - 18
            let glowScale: CGFloat = pointerCueVisible ? 1.0 : 0.7
            let glowOpacity: CGFloat = pointerCueVisible ? 1.0 : 0.0
            let tailOpacity: CGFloat = pointerCueVisible ? 1.0 : 0.0
            let tailOffset: CGFloat = pointerCueVisible ? 0 : (viewModel.anchorEdge == .top ? -6 : 6)

            ZStack {
                Circle()
                    .stroke(Color.white.opacity(0.42 * glowOpacity), lineWidth: 2)
                    .frame(width: 18, height: 18)
                    .scaleEffect(glowScale)
                    .position(x: anchorX, y: glowY)

                Circle()
                    .fill(Color.white.opacity(0.22 * glowOpacity))
                    .frame(width: 64, height: 64)
                    .blur(radius: 18)
                    .position(x: anchorX, y: glowY)

                PointerTailShape(edge: viewModel.anchorEdge)
                    .fill(
                        LinearGradient(
                            colors: [
                                Color.white.opacity(0.26),
                                Color.white.opacity(0.08),
                            ],
                            startPoint: viewModel.anchorEdge == .top ? .top : .bottom,
                            endPoint: viewModel.anchorEdge == .top ? .bottom : .top
                        )
                    )
                    .frame(width: 28, height: 18)
                    .overlay(
                        PointerTailShape(edge: viewModel.anchorEdge)
                            .stroke(Color.white.opacity(0.22), lineWidth: 1)
                            .frame(width: 28, height: 18)
                    )
                    .opacity(tailOpacity)
                    .offset(y: tailOffset)
                    .position(x: anchorX, y: tailY)
            }
        }
        .allowsHitTesting(false)
    }

    private func updatePointerCueVisibility(animated: Bool) {
        let update = {
            pointerCueVisible = showsPointerAnchorCue
        }

        if animated {
            withAnimation(.spring(response: 0.32, dampingFraction: 0.76)) {
                update()
            }
        } else {
            update()
        }
    }

    private func actionChip(_ title: String, action: @escaping () -> Void) -> some View {
        Button(title, action: action)
            .buttonStyle(.plain)
            .font(.system(size: 12, weight: .semibold, design: .rounded))
            .foregroundStyle(Color.white.opacity(0.82))
            .padding(.horizontal, 13)
            .padding(.vertical, 8)
            .background(
                Capsule(style: .continuous)
                    .fill(Color.white.opacity(0.075))
            )
            .overlay(
                Capsule(style: .continuous)
                    .stroke(Color.white.opacity(0.1), lineWidth: 1)
            )
    }

    private func subtleLabel(_ title: String) -> some View {
        Text(title)
            .font(.system(size: 11, weight: .semibold, design: .rounded))
            .foregroundStyle(Color.white.opacity(0.55))
            .padding(.horizontal, 10)
            .padding(.vertical, 6)
            .background(
                Capsule(style: .continuous)
                    .fill(Color.white.opacity(0.05))
            )
            .overlay(
                Capsule(style: .continuous)
                    .stroke(Color.white.opacity(0.07), lineWidth: 1)
            )
    }

    private func iconStatusChip(systemName: String, title: String?) -> some View {
        HStack(spacing: 6) {
            Image(systemName: systemName)
                .font(.system(size: 12, weight: .semibold))
            if let title {
                Text(title)
                    .font(.system(size: 11, weight: .semibold, design: .rounded))
            }
        }
        .foregroundStyle(Color.white.opacity(0.45))
        .padding(.horizontal, title == nil ? 8 : 10)
        .padding(.vertical, 7)
        .background(
            Capsule(style: .continuous)
                .fill(Color.white.opacity(0.05))
        )
        .overlay(
            Capsule(style: .continuous)
                .stroke(Color.white.opacity(0.07), lineWidth: 1)
        )
    }

    private func selectedTextChip(_ text: String) -> some View {
        Text("Selected: \(text)")
            .font(.system(size: 12, weight: .semibold, design: .rounded))
            .foregroundStyle(Color.white.opacity(0.82))
            .lineLimit(1)
            .padding(.horizontal, 12)
            .padding(.vertical, 7)
            .background(
                Capsule(style: .continuous)
                    .fill(Color.white.opacity(0.08))
            )
            .overlay(
                Capsule(style: .continuous)
                    .stroke(Color.white.opacity(0.1), lineWidth: 1)
            )
            .frame(maxWidth: .infinity, alignment: .leading)
    }

    private var contextStatusLabel: String {
        guard let context = viewModel.visualContext else {
            return "Chat"
        }
        if let selectedText = context.selected_text,
           !selectedText.isEmpty {
            return "Selection"
        }
        switch context.source {
        case "pointer-focus":
            return "Glance"
        case "window-context":
            return "Full Context"
        default:
            return "Visual"
        }
    }

    private var contextStatusIconName: String {
        guard let context = viewModel.visualContext else {
            return "text.bubble.fill"
        }
        if let selectedText = context.selected_text,
           !selectedText.isEmpty {
            return "text.cursor"
        }
        switch context.source {
        case "pointer-focus":
            return "scope"
        case "window-context":
            return "macwindow"
        default:
            return "viewfinder.circle.fill"
        }
    }

    private var routeBadgeLabel: String {
        if let classification = viewModel.routeClassification {
            if classification.mode == "command" {
                return "Command"
            }
            if classification.stack == "visual" {
                return "Visual"
            }
        }
        switch contextStatusLabel {
        case "Selection":
            return "Selection"
        case "Glance":
            return "Glance"
        case "Full Context":
            return "Full"
        default:
            return "Chat"
        }
    }

    private var routeBadgeIconName: String {
        if let classification = viewModel.routeClassification {
            if classification.mode == "command" {
                return "point.3.connected.trianglepath.dotted"
            }
            if classification.stack == "visual" {
                return "eye"
            }
        }
        return contextStatusIconName
    }

    private func compactStatusChip(_ text: String) -> some View {
        Text(text)
            .font(.system(size: 12, weight: .medium, design: .rounded))
            .foregroundStyle(Color.white.opacity(0.7))
            .lineLimit(2)
            .padding(.horizontal, 12)
            .padding(.vertical, 8)
            .background(
                RoundedRectangle(cornerRadius: 16, style: .continuous)
                    .fill(Color.white.opacity(0.06))
            )
            .overlay(
                RoundedRectangle(cornerRadius: 16, style: .continuous)
                    .stroke(Color.white.opacity(0.08), lineWidth: 1)
            )
            .frame(maxWidth: .infinity, alignment: .leading)
    }

    private func centeredCompactDetailRow(_ text: String) -> some View {
        HStack(spacing: 10) {
            Capsule(style: .continuous)
                .fill(Color.white.opacity(0.28))
                .frame(width: 6, height: 6)

            Text(text)
                .font(.system(size: 11, weight: .medium, design: .rounded))
                .foregroundStyle(Color.white.opacity(0.7))
                .lineLimit(1)

            Spacer(minLength: 0)
        }
        .padding(.horizontal, 14)
        .padding(.vertical, 8)
        .background(
            RoundedRectangle(cornerRadius: 16, style: .continuous)
                .fill(Color.white.opacity(0.05))
        )
        .overlay(
            RoundedRectangle(cornerRadius: 16, style: .continuous)
                .stroke(Color.white.opacity(0.07), lineWidth: 1)
        )
    }

    private func routeInspector(reason: String, candidates: String?) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 8) {
                subtleLabel("Route")
                Text(viewModel.routeDisplayLabel)
                    .font(.system(size: 11, weight: .semibold, design: .rounded))
                    .foregroundStyle(Color.white.opacity(0.82))
            }

            Text(reason)
                .font(.system(size: 11, weight: .medium, design: .rounded))
                .foregroundStyle(Color.white.opacity(0.58))
                .fixedSize(horizontal: false, vertical: true)

            if let candidates, !candidates.isEmpty {
                Text(candidates)
                    .font(.system(size: 10, weight: .medium, design: .rounded))
                    .foregroundStyle(Color.white.opacity(0.46))
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
        .padding(.horizontal, 14)
        .padding(.vertical, 12)
        .background(
            RoundedRectangle(cornerRadius: 18, style: .continuous)
                .fill(Color.white.opacity(0.045))
        )
        .overlay(
            RoundedRectangle(cornerRadius: 18, style: .continuous)
                .stroke(Color.white.opacity(0.07), lineWidth: 1)
        )
    }

    private var smallModeBadge: some View {
        HStack(spacing: 8) {
            Circle()
                .fill(viewModel.responseMode == .reviewed ? Color(red: 0.42, green: 0.88, blue: 0.62) : Color(red: 0.41, green: 0.73, blue: 0.96))
                .frame(width: 8, height: 8)
            Text(viewModel.responseMode == .reviewed ? "Reviewed Mode" : "Fast Mode")
                .font(.system(size: 11, weight: .semibold, design: .rounded))
                .foregroundStyle(Color.white.opacity(0.74))
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 8)
        .background(
            Capsule(style: .continuous)
                .fill(Color.white.opacity(0.07))
        )
        .overlay(
            Capsule(style: .continuous)
                .stroke(Color.white.opacity(0.09), lineWidth: 1)
        )
    }

    private var thinkingState: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(spacing: 10) {
                ProgressView()
                    .tint(.white.opacity(0.82))
                Text("Cleo is working through this request.")
                    .font(.system(size: 14, weight: .medium, design: .rounded))
                    .foregroundStyle(Color.white.opacity(0.78))
            }

            Text(viewModel.lastInteractionMode == "command"
                 ? "Actions and specialists are running, and the response will settle here when they finish."
                 : "Context is being read and the reply will appear here as soon as it is ready.")
                .font(.system(size: 13, weight: .regular, design: .rounded))
                .foregroundStyle(Color.white.opacity(0.52))
                .fixedSize(horizontal: false, vertical: true)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(16)
        .background(
            RoundedRectangle(cornerRadius: 20, style: .continuous)
                .fill(Color.white.opacity(0.04))
        )
        .overlay(
            RoundedRectangle(cornerRadius: 20, style: .continuous)
                .stroke(Color.white.opacity(0.07), lineWidth: 1)
        )
    }

    private var speechSetupCard: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(alignment: .top, spacing: 12) {
                Image(systemName: "waveform.badge.mic")
                    .font(.system(size: 16, weight: .semibold))
                    .foregroundStyle(Color.white.opacity(0.82))
                    .frame(width: 30, height: 30)
                    .background(
                        Circle()
                            .fill(Color.white.opacity(0.08))
                    )

                VStack(alignment: .leading, spacing: 4) {
                    Text("Finish Voice Setup")
                        .font(.system(size: 13, weight: .semibold, design: .rounded))
                        .foregroundStyle(Color.white.opacity(0.9))
                    Text("Wake word is off right now. Install macOS speech support once, then enable wake word from Cleo’s menu.")
                        .font(.system(size: 12, weight: .medium, design: .rounded))
                        .foregroundStyle(Color.white.opacity(0.58))
                        .fixedSize(horizontal: false, vertical: true)
                }

                Spacer()
            }

            HStack(spacing: 10) {
                actionChip("Open Speech Settings") {
                    viewModel.openSpeechSettings()
                }
                actionChip("I'll Do This Later") {
                    viewModel.dismissSpeechSetupCard()
                }
                Spacer()
                Text(viewModel.wakeWordEnabled ? "Wake word on" : "Wake word off")
                    .font(.system(size: 11, weight: .semibold, design: .rounded))
                    .foregroundStyle(Color.white.opacity(0.5))
            }
        }
        .padding(16)
        .panelSurface(cornerRadius: 22, fillOpacity: 0.055, strokeOpacity: 0.08)
    }

    private var commandTaskStrip: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("Workflow")
                .font(.system(size: 12, weight: .semibold, design: .rounded))
                .foregroundStyle(Color.white.opacity(0.68))
            ScrollView(.horizontal, showsIndicators: false) {
                HStack(spacing: 10) {
                    ForEach(Array(viewModel.commandTasks.enumerated()), id: \.offset) { _, task in
                        VStack(alignment: .leading, spacing: 6) {
                            HStack(spacing: 6) {
                                Text(task.specialist.capitalized)
                                    .font(.system(size: 11, weight: .semibold, design: .rounded))
                                    .foregroundStyle(Color.white.opacity(0.74))
                                Spacer(minLength: 4)
                                Text(task.status.capitalized)
                                    .font(.system(size: 9, weight: .bold, design: .rounded))
                                    .foregroundStyle(task.status == "completed" ? Color(red: 0.42, green: 0.88, blue: 0.62) : Color(red: 0.98, green: 0.72, blue: 0.34))
                                    .padding(.horizontal, 7)
                                    .padding(.vertical, 4)
                                    .background(
                                        Capsule(style: .continuous)
                                            .fill(Color.white.opacity(0.05))
                                    )
                            }
                            Text(task.title)
                                .font(.system(size: 12, weight: .semibold, design: .rounded))
                                .foregroundStyle(Color.white.opacity(0.9))
                                .lineLimit(2)
                                .frame(width: 160, alignment: .leading)
                            Text(actionLabel(for: task))
                                .font(.system(size: 10, weight: .medium, design: .rounded))
                                .foregroundStyle(Color.white.opacity(0.52))
                                .lineLimit(2)
                        }
                        .padding(13)
                        .frame(width: 190, alignment: .leading)
                        .panelSurface(cornerRadius: 20, fillOpacity: 0.055, strokeOpacity: 0.085)
                    }
                }
            }
        }
    }

    private var commandOutcomeStrip: some View {
        ScrollView(.horizontal, showsIndicators: false) {
            HStack(spacing: 10) {
                ForEach(Array(viewModel.commandTasks.enumerated()), id: \.offset) { _, task in
                    HStack(spacing: 8) {
                        Image(systemName: task.status == "completed" ? "checkmark.circle.fill" : "exclamationmark.triangle.fill")
                            .font(.system(size: 11, weight: .semibold))
                            .foregroundStyle(task.status == "completed" ? Color(red: 0.42, green: 0.88, blue: 0.62) : Color(red: 0.98, green: 0.72, blue: 0.34))
                        Text(actionLabel(for: task))
                            .font(.system(size: 11, weight: .semibold, design: .rounded))
                            .foregroundStyle(Color.white.opacity(0.76))
                    }
                    .padding(.horizontal, 12)
                    .padding(.vertical, 8)
                    .background(
                        Capsule(style: .continuous)
                            .fill(Color.white.opacity(0.07))
                    )
                    .overlay(
                        Capsule(style: .continuous)
                            .stroke(Color.white.opacity(0.09), lineWidth: 1)
                    )
                }
            }
        }
    }

    private var pendingWorkflowStrip: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("Workflow")
                .font(.system(size: 12, weight: .semibold, design: .rounded))
                .foregroundStyle(Color.white.opacity(0.68))
            HStack(spacing: 10) {
                ForEach(Array(viewModel.progressSteps.enumerated()), id: \.offset) { _, step in
                    HStack(spacing: 8) {
                        Circle()
                            .fill(step == viewModel.activeProgressStep ? Color(red: 0.41, green: 0.73, blue: 0.96) : Color.white.opacity(0.16))
                            .frame(width: step == viewModel.activeProgressStep ? 10 : 8, height: step == viewModel.activeProgressStep ? 10 : 8)
                        Text(step)
                            .font(.system(size: 11, weight: step == viewModel.activeProgressStep ? .semibold : .medium, design: .rounded))
                            .foregroundStyle(Color.white.opacity(step == viewModel.activeProgressStep ? 0.8 : 0.5))
                    }
                    .padding(.horizontal, 12)
                    .padding(.vertical, 8)
                    .background(
                        Capsule(style: .continuous)
                            .fill(Color.white.opacity(0.06))
                    )
                    .overlay(
                        Capsule(style: .continuous)
                            .stroke(Color.white.opacity(0.08), lineWidth: 1)
                    )
                }
            }
        }
    }

    private func actionLabel(for task: OverlayCommandTask) -> String {
        switch task.specialist {
        case "action":
            return task.status == "completed" ? "Action completed" : "Action blocked"
        case "memory":
            return "Memory updated"
        case "workspace":
            return "Workspace checked"
        case "connector":
            return "Context loaded"
        case "planner":
            return "Plan prepared"
        default:
            return task.title
        }
    }

    private func progressStrip(activeStep: String, steps: [String]) -> some View {
        HStack(spacing: 10) {
            ForEach(Array(steps.enumerated()), id: \.offset) { index, step in
                let isActive = step == activeStep
                HStack(spacing: 8) {
                    Circle()
                        .fill(isActive ? Color(red: 0.41, green: 0.73, blue: 0.96) : Color.white.opacity(0.18))
                        .frame(width: isActive ? 10 : 8, height: isActive ? 10 : 8)
                    Text(step)
                        .font(.system(size: 11, weight: isActive ? .semibold : .medium, design: .rounded))
                        .foregroundStyle(Color.white.opacity(isActive ? 0.82 : 0.52))
                }
                if index < steps.count - 1 {
                    Rectangle()
                        .fill(Color.white.opacity(0.08))
                        .frame(width: 18, height: 1)
                }
            }
        }
        .padding(.horizontal, 14)
        .padding(.vertical, 10)
        .panelSurface(cornerRadius: 18, fillOpacity: 0.055, strokeOpacity: 0.08)
        .transition(.opacity.combined(with: .move(edge: .top)))
    }

    private var resizeHandle: some View {
        Rectangle()
            .fill(Color.clear)
            .frame(width: 14)
            .overlay(
                Capsule(style: .continuous)
                    .fill(Color.white.opacity(0.08))
                    .frame(width: 4, height: 72)
            )
            .contentShape(Rectangle())
            .gesture(
                DragGesture(minimumDistance: 0)
                    .onChanged { value in
                        viewModel.resizeWorkspacePanel(by: value.translation.width * -1)
                    }
            )
    }
}

private struct WorkspacePanelView: View {
    @ObservedObject var viewModel: OverlayViewModel

    var body: some View {
        VStack(alignment: .leading, spacing: 18) {
            HStack {
                VStack(alignment: .leading, spacing: 4) {
                    Text(viewModel.memoryPanelTab == .graph ? "Graph" : "Memory")
                        .font(.system(size: 20, weight: .bold, design: .rounded))
                        .foregroundStyle(.white)
                    Text(viewModel.memoryPanelTab == .graph ? "Live graph memory and connected context." : "Profile, workflows, imports, and remembered context.")
                        .font(.system(size: 12, weight: .medium, design: .rounded))
                        .foregroundStyle(Color.white.opacity(0.56))
                }

                Spacer()

                Button {
                    viewModel.hideWorkspacePanel()
                } label: {
                    Image(systemName: "xmark")
                        .font(.system(size: 11, weight: .bold))
                        .foregroundStyle(Color.white.opacity(0.72))
                        .padding(8)
                        .background(
                            Circle()
                                .fill(Color.white.opacity(0.07))
                        )
                }
                .buttonStyle(.plain)
            }
            .padding(16)
            .panelSurface(cornerRadius: 24, fillOpacity: 0.06, strokeOpacity: 0.08)

            HStack {
                Picker("Panel", selection: $viewModel.memoryPanelTab) {
                    ForEach(OverlayMemoryPanelTab.allCases) { tab in
                        Text(tab.title).tag(tab)
                    }
                }
                .pickerStyle(.segmented)

                if viewModel.memoryPanelTab == .memory {
                    actionInlineChip("Import") {
                        viewModel.importChatGPTExport()
                    }
                }
            }
            .padding(.horizontal, 2)

            if viewModel.memoryPanelTab == .graph {
                TextField("Search graph nodes...", text: $viewModel.graphSearchQuery)
                    .textFieldStyle(.plain)
                    .font(.system(size: 13, weight: .medium, design: .rounded))
                    .foregroundStyle(.white)
                    .padding(.horizontal, 12)
                    .padding(.vertical, 10)
                    .panelSurface(cornerRadius: 16, fillOpacity: 0.06, strokeOpacity: 0.08)
            }

            if let snapshot = viewModel.memorySnapshot {
                Text("\(snapshot.graph.nodes.count) nodes • \(snapshot.imports.count) imports")
                    .font(.system(size: 12, weight: .medium, design: .rounded))
                    .foregroundStyle(Color.white.opacity(0.48))
            }

            if let importStatus = viewModel.importStatus, !importStatus.isEmpty {
                Text(importStatus)
                    .font(.system(size: 12, weight: .medium, design: .rounded))
                    .foregroundStyle(Color.white.opacity(0.74))
                    .padding(.horizontal, 12)
                    .padding(.vertical, 8)
                    .panelSurface(cornerRadius: 14, fillOpacity: 0.06, strokeOpacity: 0.08)
            }

            Group {
                if let snapshot = viewModel.memorySnapshot {
                    ZStack {
                        if viewModel.memoryPanelTab == .memory {
                            VStack(alignment: .leading, spacing: 16) {
                                memoryCard(title: "Session") {
                                    if snapshot.session.active_goal == nil,
                                       snapshot.session.active_app == nil,
                                       snapshot.session.active_tasks.isEmpty,
                                       snapshot.session.active_files.isEmpty {
                                        memoryPlaceholder("No active session state yet.")
                                    } else {
                                        if let goal = snapshot.session.active_goal, !goal.isEmpty {
                                            memoryRow("Current Goal", goal)
                                        }
                                        if let app = snapshot.session.active_app, !app.isEmpty {
                                            memoryRow("Active App", app)
                                        }
                                        if !snapshot.session.active_tasks.isEmpty {
                                            memoryRow("Active Tasks", snapshot.session.active_tasks.prefix(4).joined(separator: " • "))
                                        }
                                        if !snapshot.session.active_files.isEmpty {
                                            memoryRow("Active Files", snapshot.session.active_files.prefix(3).map { ($0 as NSString).lastPathComponent }.joined(separator: " • "))
                                        }
                                    }
                                }

                                memoryCard(title: "Preferences") {
                                    if snapshot.profile.preferences.isEmpty {
                                        memoryPlaceholder("No preferences yet.")
                                    } else {
                                        ForEach(snapshot.profile.preferences, id: \.key) { preference in
                                            memoryRow(preference.key.replacingOccurrences(of: "_", with: " "), preference.value)
                                        }
                                    }
                                }

                                memoryCard(title: "Workflows") {
                                    if snapshot.profile.workflows.isEmpty {
                                        memoryPlaceholder("No workflows yet.")
                                    } else {
                                        ForEach(snapshot.profile.workflows, id: \.name) { workflow in
                                            memoryRow(workflow.name, workflow.pattern)
                                        }
                                    }
                                }

                                memoryCard(title: "App Adapters") {
                                    if snapshot.adapters.isEmpty {
                                        memoryPlaceholder("No app adapters available.")
                                    } else {
                                        ForEach(Array(snapshot.adapters.prefix(6).enumerated()), id: \.offset) { _, adapter in
                                            VStack(alignment: .leading, spacing: 4) {
                                                Text(adapter.app_name)
                                                    .font(.system(size: 12, weight: .semibold, design: .rounded))
                                                    .foregroundStyle(Color.white.opacity(0.9))
                                                Text(adapter.actions.prefix(3).joined(separator: " • "))
                                                    .font(.system(size: 11, weight: .medium, design: .rounded))
                                                    .foregroundStyle(Color.white.opacity(0.58))
                                            }
                                            .frame(maxWidth: .infinity, alignment: .leading)
                                        }
                                    }
                                }

                                memoryCard(title: "Devices") {
                                    if snapshot.devices.isEmpty {
                                        memoryPlaceholder("No LAN devices registered yet.")
                                    } else {
                                        ForEach(Array(snapshot.devices.prefix(6).enumerated()), id: \.offset) { _, device in
                                            VStack(alignment: .leading, spacing: 4) {
                                                Text(device.name)
                                                    .font(.system(size: 12, weight: .semibold, design: .rounded))
                                                    .foregroundStyle(Color.white.opacity(0.9))
                                                Text(deviceSummary(device))
                                                    .font(.system(size: 11, weight: .medium, design: .rounded))
                                                    .foregroundStyle(Color.white.opacity(0.58))
                                            }
                                            .frame(maxWidth: .infinity, alignment: .leading)
                                        }
                                    }
                                }

                                memoryCard(title: "Routines") {
                                    if snapshot.routines.isEmpty {
                                        memoryPlaceholder("No routines configured yet.")
                                    } else {
                                        ForEach(Array(snapshot.routines.prefix(5).enumerated()), id: \.offset) { _, routine in
                                            memoryRow(routine.name, routine.trigger)
                                        }
                                    }
                                }

                                memoryCard(title: "Timeline") {
                                    if snapshot.timeline.isEmpty {
                                        memoryPlaceholder("No recent timeline activity.")
                                    } else {
                                        ForEach(Array(snapshot.timeline.prefix(6).enumerated()), id: \.offset) { _, event in
                                            memoryRow(event.title, event.app_name ?? event.recorded_at)
                                        }
                                    }
                                }

                                memoryCard(title: "Context Packs") {
                                    if snapshot.contextPacks.isEmpty {
                                        memoryPlaceholder("No context packs yet.")
                                    } else {
                                        ForEach(Array(snapshot.contextPacks.prefix(4).enumerated()), id: \.offset) { _, pack in
                                            memoryRow(pack.title, "\(pack.source_type) • \(pack.file_count) files")
                                        }
                                    }
                                }

                                memoryCard(title: "Import History") {
                                    if snapshot.imports.isEmpty {
                                        memoryPlaceholder("No imports yet.")
                                    } else {
                                        ForEach(Array(snapshot.imports.prefix(8).enumerated()), id: \.offset) { _, item in
                                            VStack(alignment: .leading, spacing: 4) {
                                                Text((item.file_path as NSString).lastPathComponent)
                                                    .font(.system(size: 12, weight: .semibold, design: .rounded))
                                                    .foregroundStyle(Color.white.opacity(0.9))
                                                Text("\(item.imported_conversations) convos • \(item.imported_user_messages) user msgs • \(item.imported_at)")
                                                    .font(.system(size: 11, weight: .medium, design: .rounded))
                                                    .foregroundStyle(Color.white.opacity(0.58))
                                            }
                                            .frame(maxWidth: .infinity, alignment: .leading)
                                        }
                                    }
                                }
                            }
                            .transition(.asymmetric(insertion: .move(edge: .leading).combined(with: .opacity), removal: .opacity))
                        } else {
                            GraphPanelView(snapshot: snapshot, viewModel: viewModel)
                                .transition(.asymmetric(insertion: .move(edge: .trailing).combined(with: .opacity), removal: .opacity))
                        }
                    }
                    .animation(.spring(response: 0.32, dampingFraction: 0.88), value: viewModel.memoryPanelTab)
                } else {
                    VStack {
                        Spacer()
                        ProgressView("Loading workspace...")
                            .tint(.white)
                            .foregroundStyle(.white)
                        Spacer()
                    }
                }
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
        }
        .padding(14)
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
        .panelSurface(cornerRadius: 28, fillOpacity: 0.05, strokeOpacity: 0.08)
    }

    private func memoryCard<Content: View>(title: String, @ViewBuilder content: () -> Content) -> some View {
        VStack(alignment: .leading, spacing: 12) {
            Text(title)
                .font(.system(size: 16, weight: .semibold, design: .rounded))
                .foregroundStyle(.white)
            content()
        }
        .padding(16)
        .frame(maxWidth: .infinity, alignment: .topLeading)
        .panelSurface(cornerRadius: 20, fillOpacity: 0.06, strokeOpacity: 0.08)
    }

    private func memoryRow(_ title: String, _ value: String) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(title)
                .font(.system(size: 12, weight: .semibold, design: .rounded))
                .foregroundStyle(Color.white.opacity(0.9))
            Text(value)
                .font(.system(size: 12, weight: .medium, design: .rounded))
                .foregroundStyle(Color.white.opacity(0.64))
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    private func memoryPlaceholder(_ text: String) -> some View {
        Text(text)
            .font(.system(size: 13, weight: .medium, design: .rounded))
            .foregroundStyle(Color.white.opacity(0.5))
    }

    private func deviceSummary(_ device: OverlayLANDevice) -> String {
        let parts: [String?] = [
            device.device_type,
            device.hostname,
            device.protocols.isEmpty ? nil : device.protocols.prefix(3).joined(separator: " • "),
        ]
        return parts.compactMap { value in
            guard let value, !value.isEmpty else { return nil }
            return value
        }.joined(separator: " • ")
    }

    private func actionInlineChip(_ title: String, action: @escaping () -> Void) -> some View {
        Button(title, action: action)
            .buttonStyle(.plain)
            .font(.system(size: 11, weight: .semibold, design: .rounded))
            .foregroundStyle(Color.white.opacity(0.8))
            .padding(.horizontal, 10)
            .padding(.vertical, 7)
            .background(
                Capsule(style: .continuous)
                    .fill(Color.white.opacity(0.07))
            )
            .overlay(
                Capsule(style: .continuous)
                    .stroke(Color.white.opacity(0.09), lineWidth: 1)
            )
    }
}

private struct GraphPanelView: View {
    let snapshot: OverlayMemorySnapshot
    @ObservedObject var viewModel: OverlayViewModel
    @State private var hoveredNodeID: String?

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            HStack {
                Text("\(snapshot.graph.nodes.count) nodes • \(snapshot.graph.edges.count) edges")
                    .font(.system(size: 13, weight: .semibold, design: .rounded))
                    .foregroundStyle(Color.white.opacity(0.82))
                Spacer()
            }

            HStack(alignment: .top, spacing: 16) {
                graphCanvas
                graphDetail
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
    }

    private var graphCanvas: some View {
        GeometryReader { geometry in
            let groups = filteredGroups
            let columnWidth = geometry.size.width / CGFloat(max(groups.count, 1))
            let positions = layoutPositions(size: geometry.size, columnWidth: columnWidth)

            ZStack {
                ForEach(Array(filteredEdges.enumerated()), id: \.offset) { _, edge in
                    if let source = positions[edge.source], let target = positions[edge.target] {
                        GraphEdgeLine(source: source, target: target)
                            .stroke(Color.white.opacity(0.12), lineWidth: 1)
                    }
                }

                ForEach(filteredNodes, id: \.id) { node in
                    if let point = positions[node.id] {
                        VStack(spacing: 4) {
                            Circle()
                                .fill(color(for: node.group))
                                .frame(width: viewModel.selectedGraphNodeID == node.id ? 18 : 14, height: viewModel.selectedGraphNodeID == node.id ? 18 : 14)
                            Text(node.label)
                                .font(.system(size: 11, weight: .semibold, design: .rounded))
                                .foregroundStyle(Color.white.opacity(0.88))
                                .multilineTextAlignment(.center)
                                .frame(width: 110)
                        }
                        .scaleEffect(viewModel.selectedGraphNodeID == node.id ? 1.08 : hoveredNodeID == node.id ? 1.04 : 1.0)
                        .shadow(color: Color.white.opacity(hoveredNodeID == node.id ? 0.16 : 0.0), radius: 10, x: 0, y: 4)
                        .onHover { inside in
                            withAnimation(.easeOut(duration: 0.16)) {
                                hoveredNodeID = inside ? node.id : (hoveredNodeID == node.id ? nil : hoveredNodeID)
                            }
                        }
                        .onTapGesture {
                            withAnimation(.spring(response: 0.28, dampingFraction: 0.86)) {
                                viewModel.selectedGraphNodeID = node.id
                            }
                        }
                        .position(point)
                    }
                }
            }
        }
        .frame(minHeight: 340)
        .padding(16)
        .panelSurface(cornerRadius: 20, fillOpacity: 0.05, strokeOpacity: 0.08)
    }

    private var graphDetail: some View {
        let node = filteredNodes.first { $0.id == viewModel.selectedGraphNodeID } ?? filteredNodes.first
        let relatedEdges = filteredEdges.filter { edge in
            edge.source == node?.id || edge.target == node?.id
        }

        return VStack(alignment: .leading, spacing: 12) {
            Text("Selected Node")
                .font(.system(size: 14, weight: .semibold, design: .rounded))
                .foregroundStyle(.white)

            if let node {
                HStack(spacing: 10) {
                    Circle()
                        .fill(color(for: node.group))
                        .frame(width: 12, height: 12)
                    VStack(alignment: .leading, spacing: 2) {
                        Text(node.label)
                            .font(.system(size: 13, weight: .semibold, design: .rounded))
                            .foregroundStyle(Color.white.opacity(0.88))
                        Text("\(node.kind) • \(node.group)")
                            .font(.system(size: 11, weight: .medium, design: .rounded))
                            .foregroundStyle(Color.white.opacity(0.55))
                    }
                }

                if !node.metadata.isEmpty {
                    VStack(alignment: .leading, spacing: 8) {
                        ForEach(Array(node.metadata.keys.sorted()), id: \.self) { key in
                            if let value = node.metadata[key] {
                                VStack(alignment: .leading, spacing: 2) {
                                    Text(key.replacingOccurrences(of: "_", with: " ").capitalized)
                                        .font(.system(size: 10, weight: .semibold, design: .rounded))
                                        .foregroundStyle(Color.white.opacity(0.48))
                                    Text(value)
                                        .font(.system(size: 11, weight: .medium, design: .rounded))
                                        .foregroundStyle(Color.white.opacity(0.72))
                                }
                            }
                        }
                    }
                }

                Divider()
                    .overlay(Color.white.opacity(0.08))
                    .padding(.vertical, 4)

                HStack(spacing: 8) {
                    smallDetailButton("Ask Cleo") {
                        viewModel.askAboutGraphNode(label: node.label)
                    }
                    smallDetailButton("Use") {
                        viewModel.useGraphNodeInCommand(label: node.label)
                    }
                }

                Divider()
                    .overlay(Color.white.opacity(0.08))
                    .padding(.vertical, 4)

                Text("Connected Edges")
                    .font(.system(size: 14, weight: .semibold, design: .rounded))
                    .foregroundStyle(.white)
                ForEach(Array(relatedEdges.prefix(10).enumerated()), id: \.offset) { _, edge in
                    Text("\(edge.source) \(edge.relation) \(edge.target)")
                        .font(.system(size: 11, weight: .medium, design: .monospaced))
                        .foregroundStyle(Color.white.opacity(0.55))
                        .frame(maxWidth: .infinity, alignment: .leading)
                }
            } else {
                Text("No graph node selected.")
                    .font(.system(size: 12, weight: .medium, design: .rounded))
                    .foregroundStyle(Color.white.opacity(0.55))
            }
            Spacer()
        }
        .frame(width: 220, alignment: .topLeading)
        .padding(16)
        .panelSurface(cornerRadius: 20, fillOpacity: 0.05, strokeOpacity: 0.08)
    }

    private func smallDetailButton(_ title: String, action: @escaping () -> Void) -> some View {
        Button(title, action: action)
            .buttonStyle(.plain)
            .font(.system(size: 11, weight: .semibold, design: .rounded))
            .foregroundStyle(Color.white.opacity(0.8))
            .padding(.horizontal, 10)
            .padding(.vertical, 7)
            .background(
                Capsule(style: .continuous)
                    .fill(Color.white.opacity(0.07))
            )
            .overlay(
                Capsule(style: .continuous)
                    .stroke(Color.white.opacity(0.09), lineWidth: 1)
            )
    }

    private var orderedGroups: [String] {
        let groups = snapshot.graph.nodes.map(\.group)
        return Array(NSOrderedSet(array: groups)) as? [String] ?? []
    }

    private var filteredNodes: [OverlayGraphNode] {
        let query = viewModel.graphSearchQuery.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
        guard !query.isEmpty else { return snapshot.graph.nodes }
        return snapshot.graph.nodes.filter { node in
            node.label.lowercased().contains(query) ||
            node.kind.lowercased().contains(query) ||
            node.group.lowercased().contains(query) ||
            node.metadata.values.joined(separator: " ").lowercased().contains(query)
        }
    }

    private var filteredEdges: [OverlayGraphEdge] {
        let ids = Set(filteredNodes.map(\.id))
        return snapshot.graph.edges.filter { ids.contains($0.source) || ids.contains($0.target) }
    }

    private var filteredGroups: [String] {
        let groups = filteredNodes.map(\.group)
        return Array(NSOrderedSet(array: groups)) as? [String] ?? []
    }

    private func layoutPositions(size: CGSize, columnWidth: CGFloat) -> [String: CGPoint] {
        let groups = filteredGroups
        var positions: [String: CGPoint] = [:]
        for (groupIndex, group) in groups.enumerated() {
            let nodes = filteredNodes.filter { $0.group == group }
            let x = (CGFloat(groupIndex) * columnWidth) + (columnWidth / 2)
            let step = max(72, size.height / CGFloat(max(nodes.count + 1, 2)))
            for (nodeIndex, node) in nodes.enumerated() {
                let y = CGFloat(nodeIndex + 1) * step
                positions[node.id] = CGPoint(x: x, y: min(y, size.height - 30))
            }
        }
        return positions
    }

    private func color(for group: String) -> Color {
        switch group {
        case "core": return Color(red: 0.98, green: 0.72, blue: 0.34)
        case "clients": return Color(red: 0.41, green: 0.73, blue: 0.96)
        case "integrations": return Color(red: 0.42, green: 0.88, blue: 0.62)
        case "memory": return Color(red: 0.96, green: 0.49, blue: 0.64)
        case "people": return Color(red: 0.74, green: 0.60, blue: 0.98)
        case "history": return Color(red: 0.66, green: 0.66, blue: 0.72)
        default: return Color.white.opacity(0.7)
        }
    }
}

private extension View {
    func panelSurface(
        cornerRadius: CGFloat,
        fillOpacity: CGFloat,
        strokeOpacity: CGFloat
    ) -> some View {
        self
            .background(
                RoundedRectangle(cornerRadius: cornerRadius, style: .continuous)
                    .fill(Color.white.opacity(fillOpacity))
            )
            .overlay(
                RoundedRectangle(cornerRadius: cornerRadius, style: .continuous)
                    .stroke(Color.white.opacity(strokeOpacity), lineWidth: 1)
            )
    }
}

private struct GraphEdgeLine: Shape {
    let source: CGPoint
    let target: CGPoint

    func path(in rect: CGRect) -> Path {
        var path = Path()
        path.move(to: source)
        path.addLine(to: target)
        return path
    }
}

private struct PointerPromptBubble: Shape {
    let edge: OverlayAnchorEdge
    let tailFraction: CGFloat

    func path(in rect: CGRect) -> Path {
        let radius: CGFloat = 18
        let top = rect.minY + (edge == .top ? 14 : 0)
        let bottom = rect.maxY - (edge == .bottom ? 14 : 0)
        let tail = min(max(rect.minX + rect.width * tailFraction, rect.minX + 26), rect.maxX - 26)
        var path = Path()
        path.move(to: CGPoint(x: rect.minX + radius, y: top))
        if edge == .top {
            path.addLine(to: CGPoint(x: tail - 8, y: top))
            path.addQuadCurve(to: CGPoint(x: tail, y: rect.minY), control: CGPoint(x: tail - 3, y: top - 2))
            path.addQuadCurve(to: CGPoint(x: tail + 8, y: top), control: CGPoint(x: tail + 3, y: top - 2))
        }
        path.addLine(to: CGPoint(x: rect.maxX - radius, y: top))
        path.addQuadCurve(to: CGPoint(x: rect.maxX, y: top + radius), control: CGPoint(x: rect.maxX, y: top))
        path.addLine(to: CGPoint(x: rect.maxX, y: bottom - radius))
        path.addQuadCurve(to: CGPoint(x: rect.maxX - radius, y: bottom), control: CGPoint(x: rect.maxX, y: bottom))
        if edge == .bottom {
            path.addLine(to: CGPoint(x: tail + 8, y: bottom))
            path.addQuadCurve(to: CGPoint(x: tail, y: rect.maxY), control: CGPoint(x: tail + 3, y: bottom + 2))
            path.addQuadCurve(to: CGPoint(x: tail - 8, y: bottom), control: CGPoint(x: tail - 3, y: bottom + 2))
        }
        path.addLine(to: CGPoint(x: rect.minX + radius, y: bottom))
        path.addQuadCurve(to: CGPoint(x: rect.minX, y: bottom - radius), control: CGPoint(x: rect.minX, y: bottom))
        path.addLine(to: CGPoint(x: rect.minX, y: top + radius))
        path.addQuadCurve(to: CGPoint(x: rect.minX + radius, y: top), control: CGPoint(x: rect.minX, y: top))
        path.closeSubpath()
        return path
    }
}

private struct PointerTailShape: Shape {
    let edge: OverlayAnchorEdge

    func path(in rect: CGRect) -> Path {
        var path = Path()

        if edge == .top {
            path.move(to: CGPoint(x: rect.midX, y: rect.minY))
            path.addLine(to: CGPoint(x: rect.maxX, y: rect.maxY))
            path.addQuadCurve(
                to: CGPoint(x: rect.minX, y: rect.maxY),
                control: CGPoint(x: rect.midX, y: rect.maxY + 4)
            )
            path.closeSubpath()
        } else {
            path.move(to: CGPoint(x: rect.midX, y: rect.maxY))
            path.addLine(to: CGPoint(x: rect.maxX, y: rect.minY))
            path.addQuadCurve(
                to: CGPoint(x: rect.minX, y: rect.minY),
                control: CGPoint(x: rect.midX, y: rect.minY - 4)
            )
            path.closeSubpath()
        }

        return path
    }
}

struct GlassBackgroundView: NSViewRepresentable {
    func makeNSView(context: Context) -> NSVisualEffectView {
        let view = NSVisualEffectView()
        view.material = .hudWindow
        view.blendingMode = .behindWindow
        view.state = .active
        view.appearance = NSAppearance(named: .darkAqua)
        return view
    }

    func updateNSView(_ nsView: NSVisualEffectView, context: Context) {
        nsView.material = .hudWindow
        nsView.blendingMode = .behindWindow
        nsView.state = .active
        nsView.appearance = NSAppearance(named: .darkAqua)
    }
}
