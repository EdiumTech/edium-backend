package main

import (
	"encoding/json"
	"fmt"
	"io"
	"os"
)

func main() {
	var input map[string]any
	if err := json.NewDecoder(io.LimitReader(os.Stdin, 262145)).Decode(&input); err != nil || input == nil {
		fmt.Fprintln(os.Stderr, "Вход должен быть JSON-объектом.")
		os.Exit(1)
	}
	result := Solve(input)
	if result == nil {
		fmt.Fprintln(os.Stderr, "Solve должна вернуть JSON-объект.")
		os.Exit(1)
	}
	if err := json.NewEncoder(os.Stdout).Encode(result); err != nil {
		fmt.Fprintln(os.Stderr, "Результат должен содержать только JSON-значения.")
		os.Exit(1)
	}
}
