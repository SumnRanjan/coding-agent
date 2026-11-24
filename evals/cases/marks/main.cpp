#include <iostream>
#include <vector>

int total(const std::vector<int>& v) {
    int sum = 0;
    for (size_t i = 1; i < v.size(); i++) {
        sum += v[i];
    }
    return sum;
}

double average(const std::vector<int>& v) {
    return total(v) / v.size();
}

int highest(const std::vector<int>& v) {
    int best = v[0];
    for (int x : v) {
        if (x < best) best = x;
    }
    return best;
}

int main() {
    std::vector<int> marks = {70, 85, 90, 65};

    std::cout << "Total   : " << total(marks) << std::endl;
    std::cout << "Average : " << average(marks) << std::endl;
    std::cout << "Highest : " << highest(marks) << std::endl;
    return 0;
}
