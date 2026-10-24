const byte EN = 8;
const byte STEP_PIN[3] = {2, 3, 4};   // X, Y, Z on the CNC shield V3
const byte DIR_PIN[3]  = {5, 6, 7};

const unsigned long DEFAULT_PERIOD = 3000;  // microseconds per step (smaller = faster)

long remaining[3]         = {0, 0, 0};
long position_[3]         = {0, 0, 0};
int  dirSign[3]           = {1, 1, 1};
unsigned long period[3]   = {DEFAULT_PERIOD, DEFAULT_PERIOD, DEFAULT_PERIOD};
unsigned long nextStep[3] = {0, 0, 0};

char buf[48];
byte n = 0;

void startMove(int a, long steps, unsigned long p) {
  if (a < 0 || a > 2 || steps == 0) return;
  if (p > 0) period[a] = p;
  dirSign[a] = (steps > 0) ? 1 : -1;
  digitalWrite(DIR_PIN[a], steps > 0 ? LOW : HIGH);   // swap LOW/HIGH to flip direction
  remaining[a] = labs(steps);
  nextStep[a] = micros();
}

void handle(char* line) {
  char cmd = line[0];
  if (cmd == 'M') {                         // M axis steps [period_us]
    int a; long s; long p = 0;
    int k = sscanf(line + 1, "%d %ld %ld", &a, &s, &p);
    if (k >= 2 && a >= 0 && a <= 2) { startMove(a, s, p > 0 ? p : 0); Serial.println("ok"); }
    else Serial.println("err");
  } else if (cmd == 'E') {                  // E 1 = motors on, E 0 = off
    digitalWrite(EN, line[2] == '1' ? LOW : HIGH);
    Serial.println("ok");
  } else if (cmd == 'S') {                  // S = stop all
    for (int i = 0; i < 3; i++) remaining[i] = 0;
    Serial.println("ok");
  } else if (cmd == 'Z') {                  // Z = zero the position counters
    for (int i = 0; i < 3; i++) position_[i] = 0;
    Serial.println("ok");
  } else if (cmd == 'P') {                  // P = report positions
    Serial.print("pos ");
    Serial.print(position_[0]); Serial.print(' ');
    Serial.print(position_[1]); Serial.print(' ');
    Serial.println(position_[2]);
  } else {
    Serial.println("err");
  }
}

void readSerial() {
  while (Serial.available()) {
    char c = Serial.read();
    if (c == '\n') { buf[n] = 0; n = 0; if (buf[0]) handle(buf); }
    else if (c != '\r' && n < sizeof(buf) - 1) buf[n++] = c;
  }
}

void setup() {
  for (int i = 0; i < 3; i++) { pinMode(STEP_PIN[i], OUTPUT); pinMode(DIR_PIN[i], OUTPUT); }
  pinMode(EN, OUTPUT);
  digitalWrite(EN, LOW);        // motors on
  Serial.begin(115200);
  Serial.println("ready");
}

void loop() {
  readSerial();
  unsigned long now = micros();
  for (int i = 0; i < 3; i++) {
    if (remaining[i] > 0 && (long)(now - nextStep[i]) >= 0) {
      digitalWrite(STEP_PIN[i], HIGH);
      delayMicroseconds(5);
      digitalWrite(STEP_PIN[i], LOW);
      remaining[i]--;
      position_[i] += dirSign[i];
      nextStep[i] = now + period[i];
      if (remaining[i] == 0) { Serial.print("done "); Serial.println(i); }
    }
  }
}