import { StatusBar } from "expo-status-bar";
import {
  SafeAreaView,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  TouchableOpacity,
  View,
} from "react-native";

const sampleMessages = [
  {
    role: "assistant",
    text: "I can become your shared assistant layer across apps, not just a chat screen.",
  },
  {
    role: "user",
    text: "I want access from my phone, terminal, and wherever else I am.",
  },
  {
    role: "assistant",
    text: "Then the app should be the front door, with the API acting as the brain underneath it.",
  },
];

export default function App() {
  return (
    <SafeAreaView style={styles.safeArea}>
      <StatusBar style="dark" />
      <View style={styles.screen}>
        <View style={styles.hero}>
          <Text style={styles.eyebrow}>Cleo</Text>
          <Text style={styles.title}>Your assistant should feel like an app, not a tab.</Text>
          <Text style={styles.subtitle}>
            This starter keeps the intelligence in one backend and lets the phone experience lead.
          </Text>
        </View>

        <View style={styles.graphCard}>
          <Text style={styles.graphEyebrow}>Brain Graph</Text>
          <Text style={styles.graphTitle}>See how memories, apps, and tasks connect.</Text>
          <Text style={styles.graphBody}>
            The assistant can expose linked concepts instead of only chat history, so the app can
            eventually render an interactive map of what Cleo knows and why.
          </Text>
        </View>

        <ScrollView contentContainerStyle={styles.chat}>
          {sampleMessages.map((message, index) => (
            <View
              key={`${message.role}-${index}`}
              style={[
                styles.bubble,
                message.role === "assistant" ? styles.assistantBubble : styles.userBubble,
              ]}
            >
              <Text style={styles.role}>{message.role.toUpperCase()}</Text>
              <Text style={styles.message}>{message.text}</Text>
            </View>
          ))}
        </ScrollView>

        <View style={styles.composer}>
          <TextInput
            placeholder="Ask Cleo to summarize, plan, connect apps..."
            placeholderTextColor="#6b7280"
            style={styles.input}
          />
          <TouchableOpacity style={styles.button}>
            <Text style={styles.buttonText}>Send</Text>
          </TouchableOpacity>
        </View>
      </View>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  safeArea: {
    flex: 1,
    backgroundColor: "#f3efe6",
  },
  screen: {
    flex: 1,
    paddingHorizontal: 20,
    paddingBottom: 18,
    backgroundColor: "#f3efe6",
  },
  hero: {
    marginTop: 12,
    marginBottom: 18,
    padding: 20,
    borderRadius: 28,
    backgroundColor: "#132a13",
  },
  eyebrow: {
    color: "#d8f3dc",
    fontSize: 13,
    letterSpacing: 2,
    marginBottom: 10,
    fontWeight: "700",
  },
  title: {
    color: "#fefae0",
    fontSize: 30,
    lineHeight: 36,
    fontWeight: "800",
    marginBottom: 10,
  },
  subtitle: {
    color: "#d8f3dc",
    fontSize: 15,
    lineHeight: 22,
  },
  graphCard: {
    marginBottom: 18,
    padding: 18,
    borderRadius: 24,
    backgroundColor: "#fff7ed",
    borderWidth: 1,
    borderColor: "#e7d7c9",
  },
  graphEyebrow: {
    color: "#9a3412",
    fontSize: 12,
    letterSpacing: 1.4,
    marginBottom: 8,
    fontWeight: "800",
  },
  graphTitle: {
    color: "#7c2d12",
    fontSize: 20,
    lineHeight: 26,
    fontWeight: "800",
    marginBottom: 8,
  },
  graphBody: {
    color: "#7c2d12",
    fontSize: 14,
    lineHeight: 21,
  },
  chat: {
    gap: 12,
    paddingBottom: 16,
  },
  bubble: {
    borderRadius: 24,
    padding: 16,
  },
  assistantBubble: {
    backgroundColor: "#fffdf7",
    borderWidth: 1,
    borderColor: "#d6ccc2",
    marginRight: 36,
  },
  userBubble: {
    backgroundColor: "#cde5d7",
    marginLeft: 36,
  },
  role: {
    fontSize: 11,
    letterSpacing: 1.3,
    color: "#4b5563",
    marginBottom: 6,
    fontWeight: "800",
  },
  message: {
    fontSize: 16,
    lineHeight: 23,
    color: "#111827",
  },
  composer: {
    marginTop: "auto",
    paddingTop: 10,
    gap: 10,
  },
  input: {
    backgroundColor: "#fffdf7",
    borderRadius: 18,
    paddingHorizontal: 16,
    paddingVertical: 14,
    fontSize: 16,
    borderWidth: 1,
    borderColor: "#d6ccc2",
  },
  button: {
    backgroundColor: "#31572c",
    borderRadius: 18,
    alignItems: "center",
    paddingVertical: 14,
  },
  buttonText: {
    color: "#fefae0",
    fontSize: 16,
    fontWeight: "800",
  },
});
