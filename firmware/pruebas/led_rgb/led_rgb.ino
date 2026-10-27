// HIDRO360 - prueba del LED RGB (IO42). Orden de color NEO_RGB: si rojo/verde salen cambiados, pruebe NEO_GRB
// (los ejemplos Demo/Example_15 usan NEO_GRB, Demo/Example_06 usa NEO_GRBW). No compilado ni probado aqui.
#include <Adafruit_NeoPixel.h>

#define PIN_LED 42
#define NUM_LEDS 1

Adafruit_NeoPixel led(NUM_LEDS, PIN_LED, NEO_RGB + NEO_KHZ800);

void setup() {
  led.begin();
  led.setBrightness(40);
  led.clear();
  led.show();
}

void loop() {
  led.setPixelColor(0, led.Color(255, 0, 0)); // Rojo
  led.show();
  delay(1000);

  led.setPixelColor(0, led.Color(0, 255, 0)); // Verde
  led.show();
  delay(1000);

  led.setPixelColor(0, led.Color(0, 0, 255)); // Azul
  led.show();
  delay(1000);
}