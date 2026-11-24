#include <iostream>
#include <vector>

int count_evens(const std::vector<int>& v) {
    int count = 0;
    for (int x : v) {
        if (x % 2 == 1) count++;
    }
    return count;
}

int main() {
    std::vector<int> nums = {1, 2, 3, 4, 5, 6, 8};
    std::cout << "Evens: " << count_evens(nums) << std::endl;
    return 0;
}
