import java.util.*;
class Main{
    public static void main(String[] args){
        Scanner sc = new Scanner(System.in);
        int n=sc.nextInt();
        int[] arr = new int[n];
        
        for(int i=0;i<n;i++){
            arr[i]=sc.nextInt();
        }
        
        Map<Integer,Integer> map = new HashMap<>();
        
        for(int x : arr){
            map.put(x,map.getOrDefault(x,0) + 1);
        }
        
        for(int x : map.keySet()){
            if(map.get(x) > n / 2){
                System.out.println("Majority elment:"+ x);
                return;
            }
        }
    }
}